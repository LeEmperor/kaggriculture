(* The Kaggriculture action builder for the native simulator. It shares [Actions.emits]
   with the JSON seam, so load-time action validation is identical, then constructs the
   typed [Kag_model.Model.player_action] surface directly. *)

open Policy_dsl.Expr
module Model = Kag_model.Model

let emits = Actions.emits

let action_error (firing : Policy_dsl.Cascade.firing) =
  failf
    "action"
    "rule '%s' emitted unsupported native action %s"
    firing.fired_rule
    firing.fired_op
;;

let worker_action (firing : Policy_dsl.Cascade.firing) =
  match firing.fired_op, firing.fired_operands with
  | "PASS", [] -> Model.Unit_pass
  | "DROP", [] -> Model.Drop
  | "HARVEST", [] -> Model.Harvest
  | "WATER", [] -> Model.Water
  | "PLANT", [ Vstr crop ] ->
    Model.Plant_crop { crop = Native_vocabulary.crop_index_of_name crop }
  | _ -> action_error firing
;;

let market_order (firing : Policy_dsl.Cascade.firing) =
  match firing.fired_op, firing.fired_operands with
  | "BUY_SEED", [ Vstr crop; Vint count ] when count >= 1 ->
    Model.Buy_seed { crop = Native_vocabulary.crop_index_of_name crop; count }
  | "SELL", [ Vstr item; Vint count ] when count >= 1 ->
    Model.Sell { item = Native_vocabulary.product_index item; count }
  | ("BUY_SEED" | "SELL"), operands ->
    let units =
      match List.rev operands with
      | value :: _ -> value_name value
      | [] -> "<missing>"
    in
    failf
      "action"
      "market rule '%s' emitted %s units; action units must be a positive integer"
      firing.fired_rule
      units
  | _ -> action_error firing
;;

let build_action (turn : Policy_dsl.Interpreter.turn) : Model.player_action =
  { farmer =
      (match turn.farmer with
       | None -> Model.Unit_pass
       | Some firing -> worker_action firing)
  ; hands = [||]
  ; market = Array.of_list (List.map market_order turn.market)
  }
;;
