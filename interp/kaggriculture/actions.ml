(* The Kaggriculture action seam: what a rule may emit, and how it is shaped.

   [emits] is the static half, consumed by Family.load so that a misspelled ["SEL", ...] or
   a ["PLANT"] missing its crop fails at load rather than mid-episode. [build_action] is
   the runtime half, turning the decide stage's firings into the list-encoded action the
   environment expects. The port of submission/actions.py, and the mirror of the typed emit
   constructors in authoring/kaggriculture/actions.ml.

   Like vocabulary.ml this file names crops and actions, and like that file it is
   deliberately separable from policy_dsl/ by deletion. *)

open Policy_dsl.Expr

let worker_emits =
  [ "PASS", []; "DROP", []; "HARVEST", []; "WATER", []; "PLANT", [ str_kind ] ]
;;

let market_emits = [ "BUY_SEED", [ str_kind; int_kind ]; "SELL", [ str_kind; int_kind ] ]

let of_map pairs =
  List.fold_left (fun map (key, value) -> SM.add key value map) SM.empty pairs
;;

let emits =
  of_map
    [ Policy_dsl.Family.farmer, of_map worker_emits
    ; Policy_dsl.Family.market, of_map market_emits
    ]
;;

let operand_json = value_json

let firing_json (firing : Policy_dsl.Cascade.firing) =
  `List (`String firing.fired_op :: List.map operand_json firing.fired_operands)
;;

(* Market orders carry a unit count as their last operand; worker actions do not. The
   family's kind checking cannot see this — an int operand is an int operand — so the one
   thing left to assert at emit time is that the count is positive. *)
let check_market_units (firing : Policy_dsl.Cascade.firing) =
  match List.rev firing.fired_operands with
  | Vint units :: _ when units >= 1 -> ()
  | last :: _ ->
    failf
      "action"
      "market rule '%s' emitted %s units; action units must be a positive integer"
      firing.fired_rule
      (value_name last)
  | [] -> failf "action" "market rule '%s' emitted no operands" firing.fired_rule
;;

(* Shape one turn's firings into the environment's action encoding.

   A farmer cascade with no matching rule passes, which is the same default the
   hand-written policy falls through to. A well-formed family ends its cascade with a
   catch-all, so this is a safety net rather than a normal path. *)
let build_action (turn : Policy_dsl.Interpreter.turn) : Yojson.Safe.t =
  let farmer =
    match turn.farmer with
    | None -> `List [ `String "PASS" ]
    | Some firing -> firing_json firing
  in
  let market =
    List.map
      (fun firing ->
        check_market_units firing;
        firing_json firing)
      turn.market
  in
  `Assoc [ "farmer", farmer; "hands", `List []; "market", `List market ]
;;
