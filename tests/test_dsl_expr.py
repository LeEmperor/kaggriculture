"""The expression language, the rule groups, and the staged register machine."""

from __future__ import annotations

import unittest

from submission.dsl import cascade, expr, pipeline

MODE = expr.Kind("str", frozenset({"OPENING", "PRODUCTION", "LIQUIDATION"}))


def type_env(**overrides: object) -> expr.TypeEnv:
    defaults: dict[str, object] = {
        "params": {
            "threshold": expr.INT,
            "crop": expr.Kind("str", frozenset({"WHEAT"})),
        },
        "registers": {"mode": MODE, "count": expr.INT, "seen": expr.BOOL},
        "observations": {"day": expr.INT, "price": expr.INT, "wet": expr.BOOL},
        "stage": "decide",
    }
    defaults.update(overrides)
    return expr.TypeEnv(**defaults)  # type: ignore[arg-type]


def env(**overrides: object) -> expr.Env:
    defaults: dict[str, object] = {
        "params": {"threshold": 25, "crop": "WHEAT"},
        "state": {"mode": "OPENING", "count": 3, "seen": False},
        "observe": {"day": 10, "price": 30, "wet": True}.__getitem__,
    }
    defaults.update(overrides)
    return expr.Env(**defaults)  # type: ignore[arg-type]


class ParseTest(unittest.TestCase):
    def test_leaves_and_operators_round_trip(self) -> None:
        parsed = expr.parse(["max", ["state", "count"], ["const", 7]])
        self.assertEqual(
            parsed, expr.Op("max", (expr.State("count"), expr.Const(7)))
        )

    def test_float_and_null_constants_are_rejected(self) -> None:
        # docs/policy_dsl.md writes ["const", null] and ["const", 0.0] in its
        # worked encoding, but its own leaf specification admits neither.
        for literal in (0.0, None, [], {}):
            with self.assertRaises(expr.DslError):
                expr.parse(["const", literal])

    def test_arity_is_checked(self) -> None:
        with self.assertRaises(expr.DslError):
            expr.parse(["not", ["const", True], ["const", False]])
        with self.assertRaises(expr.DslError):
            expr.parse(["if", ["const", True], ["const", 1]])

    def test_unknown_head_is_rejected(self) -> None:
        with self.assertRaises(expr.DslError):
            expr.parse(["/", ["const", 4], ["const", 2]])


class InferTest(unittest.TestCase):
    def test_ordering_requires_integers(self) -> None:
        with self.assertRaises(expr.DslError):
            expr.infer(expr.parse(["<", ["obs", "wet"], ["const", 1]]), type_env())

    def test_booleans_are_not_integers(self) -> None:
        with self.assertRaises(expr.DslError):
            expr.infer(expr.parse(["+", ["obs", "wet"], ["const", 1]]), type_env())

    def test_misspelled_enum_member_is_caught_at_load(self) -> None:
        # The whole reason enum domains are tracked: this would otherwise be a
        # guard that is silently false for an entire episode.
        with self.assertRaises(expr.DslError):
            expr.infer(
                expr.parse(["==", ["state", "mode"], ["const", "LIQUIDATON"]]),
                type_env(),
            )
        self.assertEqual(
            expr.infer(
                expr.parse(["==", ["state", "mode"], ["const", "LIQUIDATION"]]),
                type_env(),
            ),
            expr.BOOL,
        )

    def test_unknown_names_name_what_is_declared(self) -> None:
        with self.assertRaises(expr.DslError) as caught:
            expr.infer(expr.parse(["obs", "opponent_money"]), type_env())
        self.assertIn("day", str(caught.exception))

    def test_next_is_restricted_to_observe_and_commit(self) -> None:
        with self.assertRaises(expr.DslError):
            expr.infer(expr.parse(["next", "mode"]), type_env(stage="decide"))
        with self.assertRaises(expr.DslError):
            expr.infer(
                expr.parse(["next", "mode"]),
                type_env(stage="observe", next_available=frozenset()),
            )
        self.assertEqual(
            expr.infer(
                expr.parse(["next", "mode"]),
                type_env(stage="observe", next_available=frozenset({"mode"})),
            ),
            MODE,
        )

    def test_fired_leaves_are_commit_only(self) -> None:
        groups = {"farmer": frozenset({"plant_seed", "idle"})}
        with self.assertRaises(expr.DslError):
            expr.infer(
                expr.parse(["fired", "farmer"]),
                type_env(stage="observe", groups=groups),
            )
        with self.assertRaises(expr.DslError):
            expr.infer(
                expr.parse(["fired?", "farmer", "no_such_rule"]),
                type_env(stage="commit", groups=groups),
            )
        self.assertEqual(
            expr.infer(
                expr.parse(["fired?", "farmer", "idle"]),
                type_env(stage="commit", groups=groups),
            ),
            expr.BOOL,
        )


class EvaluateTest(unittest.TestCase):
    def test_arithmetic_and_comparison(self) -> None:
        self.assertEqual(
            expr.evaluate(
                expr.parse(["-", ["obs", "price"], ["param", "threshold"]]), env()
            ),
            5,
        )
        self.assertIs(
            expr.evaluate(
                expr.parse([">=", ["obs", "price"], ["param", "threshold"]]), env()
            ),
            True,
        )

    def test_and_short_circuits_before_touching_later_operands(self) -> None:
        # Load-bearing: harvest_ready guards tile_planted_day behind tile_is_plant.
        asked: list[str] = []

        def observe(name: str) -> object:
            asked.append(name)
            return {"wet": False, "day": 10}[name]

        result = expr.evaluate(
            expr.parse(["and", ["obs", "wet"], [">", ["obs", "day"], ["const", 0]]]),
            env(observe=observe),
        )
        self.assertIs(result, False)
        self.assertEqual(asked, ["wet"])

    def test_if_evaluates_only_the_taken_branch(self) -> None:
        asked: list[str] = []

        def observe(name: str) -> object:
            asked.append(name)
            return 10

        expr.evaluate(
            expr.parse(
                ["if", ["const", True], ["const", 1], ["obs", "day"]]
            ),
            env(observe=observe),
        )
        self.assertEqual(asked, [])


class CascadeTest(unittest.TestCase):
    def rules(self) -> list[cascade.Rule]:
        return [
            cascade.Rule(
                "hot",
                expr.parse([">", ["obs", "price"], ["const", 25]]),
                cascade.Emit("SELL", (expr.parse(["param", "crop"]),)),
            ),
            cascade.Rule(
                "always",
                expr.parse(["const", True]),
                cascade.Emit("PASS", ()),
            ),
        ]

    def test_first_match_wins(self) -> None:
        firing = cascade.select_first(self.rules(), env())
        assert firing is not None
        self.assertEqual(firing.rule, "hot")
        self.assertEqual(firing.op, "SELL")
        self.assertEqual(firing.operands, ("WHEAT",))

    def test_all_match_keeps_declaration_order(self) -> None:
        firings = cascade.select_all(self.rules(), env())
        self.assertEqual(cascade.fired_names(firings), ("hot", "always"))

    def test_no_match_is_representable(self) -> None:
        rules = [
            cascade.Rule(
                "never", expr.parse(["const", False]), cascade.Emit("PASS", ())
            )
        ]
        self.assertIsNone(cascade.select_first(rules, env()))
        self.assertEqual(cascade.select_all(rules, env()), ())


class PipelineTest(unittest.TestCase):
    def make_env(self, state, written):  # type: ignore[no-untyped-def]
        return expr.Env(
            params={},
            state=state,
            observe={"price": 30}.__getitem__,
            next=written,
        )

    def test_writes_within_a_stage_commit_simultaneously(self) -> None:
        # The shift register from docs/policy_dsl.md. If writes were sequential
        # every lag would collapse to the newest price.
        writes = [
            pipeline.Write("d1", expr.parse(["obs", "price"])),
            pipeline.Write("d2", expr.parse(["state", "d1"])),
            pipeline.Write("d3", expr.parse(["state", "d2"])),
        ]
        state = {"d1": 3, "d2": 2, "d3": 1}
        self.assertEqual(
            pipeline.run_writes(writes, state, self.make_env),
            {"d1": 30, "d2": 3, "d3": 2},
        )

    def test_next_reads_the_write_made_earlier_in_the_stage(self) -> None:
        writes = [
            pipeline.Write("d1", expr.parse(["obs", "price"])),
            pipeline.Write("d2", expr.parse(["next", "d1"])),
        ]
        result = pipeline.run_writes(writes, {"d1": 3, "d2": 2}, self.make_env)
        self.assertEqual(result, {"d1": 30, "d2": 30})

    def test_unwritten_registers_are_carried_forward(self) -> None:
        result = pipeline.run_writes(
            [pipeline.Write("d1", expr.parse(["const", 9]))],
            {"d1": 0, "kept": 7},
            self.make_env,
        )
        self.assertEqual(result["kept"], 7)


if __name__ == "__main__":
    unittest.main()
