(* funkystrat-v1: a minimal family, grown one decision at a time.

   It started as a pass-only control — the floor a real strategy has to beat — and now
   carries exactly one decision: buy a seed, plant it, then go back to napping. It stays
   small on purpose, as the worked example of what each of Family.create's labelled
   arguments is for.

   The structural point: there is no strategy *class* here and no `act`. The class
   dissolved into this one value, and `act` lives once, frozen, in
   submission/dsl/interpreter.py. What a family author writes is only the contents of the
   four stages. *)

open Policy_family
open Expr.O
module V = Kaggriculture.Vocabulary
module A = Kaggriculture.Actions

(* Parameters == PolicyParameters' fields. The ~values/~min/~max are what
   PolicyParameters.validate() spells out as raised exceptions; here the constraint is
   declared rather than coded, and both the OCaml types and the Python loader enforce it.

   `crop` is not optional in the way it looks. Four accessors in submission/vocabulary.py
   — seeds, shed_units, market_price, seed_cost — resolve their item through
   parameters["crop"], so any family reading one of them must declare a parameter with
   exactly this name. *)
(* forms an Expr.param type based on a skind -> string-kind *)
let (crop : Expr.skind Expr.param) = Expr.Param.enum "crop" ~values:[ "WHEAT" ]

(* Registers == PolicyState's fields, with ~init playing the dataclass default.

   `cls` has no Python counterpart and is the one extra obligation the DSL adds: Decision
   means some guard reads it, so every backend must agree on it exactly; Telemetry means
   it is written but never read by a decision, and may diverge harmlessly. `seeds_bought`
   is Decision because stock_up's guard reads it — that is the promotion rule from
   docs/policy_dsl.md. *)
(* ikind -> integer kind, forms a register for storing model memory *)
let (last_step : Expr.ikind Expr.reg) =
  (Expr.Reg.int "last_step" ~init:(-1) ~cls:Expr.Decision : Expr.ikind Expr.reg)
;;

let _policy_state : Family.packed_reg list = [ R last_step ]

(* this type annotation says that the last_step is equal to an expression, said expression
   is of type Expr.iking Expr.reg explicitly, which is nice
*)

let turns_seen : Expr.ikind Expr.reg =
  Expr.Reg.int "turns_seen" ~init:0 ~cls:Expr.Telemetry
;;
let (seeds_bought : Expr.bkind Expr.reg) =
  Expr.Reg.bool "seeds_bought" ~init:false ~cls:Expr.Decision
;;
let w = Family.write

(* ------------------------------------------------------------------ *)
(* One rule, built by hand outside the family, part by part. *)
(* ------------------------------------------------------------------ *)

(* 1. The guard. `when_` is just an expression that evaluates to a bool, so its type is
      [Expr.bkind Expr.t] — nothing rule-specific about it. The same value would be
      equally legal as a `reset_when`, or as the condition of an `if_` inside a register
      write. A rule does not own its guard; it just holds one.

   forms boolean composability over a set of conditions that it's fed or invokes
*)
let funky_when : Expr.bkind Expr.t =
  and_ [ obs V.shed_units >: int 0; obs V.market_price >=: int 30 ]
;;

(* 2. The emit: an action head plus its operands.

   [Family.O] is the box that lets operands of different kinds share one list.
   [param crop] is an [skind Expr.t] and [obs V.shed_units] is an [ikind Expr.t] — two
   different types, so OCaml will not put them in a list together. Wrapping each in [O]
   erases the kind and makes both an [operand], which is what makes ["SELL", crop, units]
   expressible at all.

   This is the generic constructor, which is why the head is an unchecked string:
   [Family.emit "nada" []] compiles fine and is caught only by the Python loader. [A.sell]
   below is this same call with the head and the operand kinds already fixed, so a wrong
   arity fails at build time instead. Prefer it; this spelling is here to show what it
   expands to. *)

(* sets an emit of the "SELL" op, composed of an "operand list"

   Family.O -> Operand, takes in an Expr.t, and returns an operand Family.O -> Operand,
   takes in (obvs V.shed_units)

   (param crop) -> the arg passed into the operand type designates
   (obs V.shed_units) -> an observation; obs takes in an Obs, which is very basic
    struct that contains (2) member functions
      "int", which takes a param "name" -> returns a record with the name passed through,
      and the "kind" set to Int
    V.shed_units -> Obs.int "shed_units" -> carries the name "shed_units", and the o_kind
    hard-mapped to Int



   These can be thought of as an action that is taken in response to a given state+input map

  A Family.rule binds together an "emit" and a "when_", which represents a stimulus + reaction
  whne that stimulus is true.
*)
let funky_emit : Family.emit =
  Family.emit "SELL" [ Family.O (param crop); Family.O (obs V.shed_units) ]
;;

(* 3. The rule: a name, the guard, the emit. The name is not decoration — it is the handle
   `fired_any` uses from the commit stage, and it must be unique within its group.


  "when `when_` happens, output `emit`"
*)
let funky_rule : Family.rule =
  Family.rule ~name:"mashallah_rule" ~when_:funky_when ~emit:funky_emit
;;

(* The typed equivalent of the three steps above, as one expression. Same JSON. *)
let _funky_rule_typed : Family.rule =
  Family.rule
    ~name:"mashallah_rule"
    ~when_:(and_ [ obs V.shed_units >: int 0; obs V.market_price >=: int 30 ])
    ~emit:(A.sell ~crop:(param crop) ~units:(obs V.shed_units))
;;

(* owning toplevel of the strategy - might rename to "strategy" later *)
let family : Family.t =
  (Family.create
     ~policy_id:"funkystrat-v1"
     ~family:"funkystrat"
     ~family_version:1
     ~parameters:[ P crop ]
     ~registers:[ R last_step; R turns_seen; R seeds_bought ]
       (* reset_when == the `step == 0 and last_step >= 0` test at the top of
          MonocropReorder.act. Restoring every ~init is what `self.state = PolicyState()`
          did. *)
     ~reset_when:(and_ [ obs V.step ==: int 0; state last_step >=: int 0 ])
       (* observe == _observe(). Empty here: no decision below reads a derived value, so
          there is nothing to precompute. Writes made here ARE visible to the rules below,
          which is the whole reason this stage exists. *)
     ~observe:[]
       (* market_rules == _market_actions(). select_all: every matching rule fires, so
          these are independent orders, not alternatives. *)
     ~market_rules:
       ([ Family.rule
            ~name:"stock_up"
            ~when_:(and_ [ not_ (state seeds_bought); obs V.money >=: int 500 ])
            ~emit:(A.buy_seed ~crop:(param crop) ~units:(int 1))
        ; funky_rule
        ]
        : Family.rule list)
       (* farmer_cascade == _farmer_action(). select_first: list order IS the priority
          order, exactly like that function's chain of early returns. `idle` is the final
          `return pass_worker()`. *)
     ~farmer_cascade:
       [ Family.rule
           ~name:"plant_it"
           ~when_:(and_ [ obs V.tile_is_empty; obs V.seeds >: int 0 ])
           ~emit:(A.plant ~crop:(param crop))
       ; Family.rule ~name:"idle" ~when_:(bool true) ~emit:A.pass
       ]
       (* commit == _record_requested_actions(), plus act()'s trailing bookkeeping.
          `fired`/`fired_any` are legal only here, because only here is it known which
          rules fired. Writes land simultaneously and are NOT visible to this turn's
          decisions. *)
     ~commit:
       [ w last_step (obs V.step)
       ; w turns_seen (state turns_seen +: int 1)
       ; w seeds_bought (or_ [ state seeds_bought; fired_any Expr.Market "stock_up" ])
       ]
   : Family.t)
;;
