(* The native vocabulary cannot be covered by JSON golden vectors. Compare all fifteen
   accessors against the proven JSON vocabulary on projections of the same model state,
   including a non-default board stride and a live plant tile. *)

open Policy_dsl
module Model = Kag_model.Model

let failures = ref 0

let check condition message =
  if condition
  then Printf.printf "PASS %s\n" message
  else (
    incr failures;
    Printf.printf "FAIL %s\n" message)
;;

let parameters = Expr.SM.singleton "crop" (Expr.Vstr "WHEAT")

let compare_accessors state label =
  let native_observation = Model.observe state ~player:0
  and json_observation = Kag_serialize.observation state ~player:0 in
  Expr.SM.iter
    (fun name _ ->
      let json =
        (Expr.SM.find name Kag_vocabulary.Vocabulary.t.Interpreter.accessors)
          json_observation
          parameters
      and native =
        (Expr.SM.find name Kag_vocabulary.Native_vocabulary.t.Interpreter.accessors)
          native_observation
          parameters
      in
      check (json = native) (Printf.sprintf "%s accessor %s" label name))
    Kag_vocabulary.Vocabulary.t.Interpreter.kinds
;;

let native_observation_matches_json () =
  check
    (Kag_vocabulary.Native_vocabulary.product_names = Kag_serialize.product_names)
    "native product table matches serializer";
  check
    (Kag_vocabulary.Native_vocabulary.shed_item_names = Kag_serialize.shed_item_names)
    "native shed-item table matches serializer";
  let config = { Model.default_config with Model.board_size = 8 } in
  let state = Model.initial_state config in
  let observation = Model.observe state ~player:0 in
  check (observation.Model.obs_board_size = 8) "observation carries board size";
  compare_accessors state "empty board8";
  let farm = state.Model.farms.(0) in
  let index = (farm.Model.farmer_y * config.Model.board_size) + farm.Model.farmer_x in
  farm.Model.money <- 2875.0;
  farm.Model.tiles.(index)
  <- Model.Plant
       { crop = 0
       ; planted_day = 0
       ; watered_today = true
       ; consecutive_unwatered = 0
       ; yield_units = 3
       ; max_lifespan_step = 100
       ; fertilized_until_day = -1
       };
  state.Model.privates.(0).Model.seeds.(0) <- 4;
  state.Model.privates.(0).Model.shed.(0) <- 7;
  state.Model.privates.(0).Model.inventories.(0).Model.counts.(0) <- 2;
  state.Model.market_prices.(0) <- 31;
  compare_accessors state "plant board8"
;;

let firing fired_rule fired_op fired_operands : Cascade.firing =
  { fired_rule; fired_op; fired_operands }
;;

let native_actions_match_json () =
  let turn : Interpreter.turn =
    { farmer = Some (firing "plant_seed" "PLANT" [ Expr.Vstr "WHEAT" ])
    ; market =
        [ firing "sell_stock" "SELL" [ Expr.Vstr "WHEAT"; Expr.Vint 3 ]
        ; firing "reorder_seeds" "BUY_SEED" [ Expr.Vstr "WHEAT"; Expr.Vint 4 ]
        ]
    }
  in
  let json = Kag_vocabulary.Actions.build_action turn
  and native =
    Kag_vocabulary.Native_actions.build_action turn |> Kag_serialize.json_of_player_action
  in
  check
    (Kag_serialize.normalize json = Kag_serialize.normalize native)
    "native action shape"
;;

let () =
  native_observation_matches_json ();
  native_actions_match_json ();
  if !failures > 0
  then (
    Printf.printf "%d failure(s)\n" !failures;
    exit 1)
  else print_endline "all native vocabulary tests passed"
;;
