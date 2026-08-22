(* Three suites: the model's reference facts, the rule-group-1 differential comparison
   against oracle-recorded fixtures (model_group1_fixture.json — regenerate with
   record_model_fixture.py, never by hand), and Python_random against draws recorded from
   CPython itself (python_random_fixture.json).

   The RNG fixture stores floats as Python float.hex() strings and compares exactly:
   bit-for-bit agreement is the entire point, an approximate MT19937 is worthless for
   differential replay. The model fixture is compared just as strictly — canonicalized
   JSON equality at initialization, then per-turn day/hour/step/status/reward across whole
   PASS episodes. *)

open Kag_model

let failures = ref 0

let check condition message =
  if condition
  then Printf.printf "PASS %s\n" message
  else (
    incr failures;
    Printf.printf "FAIL %s\n" message)
;;

(* ---------------- model scaffold ---------------- *)

let initialization_matches_reference () =
  let state = Model.initial_state Model.default_config in
  check (state.Model.status = Model.Active) "initial status";
  check (state.Model.transitions = 0) "initial transition count";
  check (state.Model.resolved_seed = 0) "initial resolved seed";
  check (state.Model.town_shop_count = 0) "initial town has no shops";
  Array.iter
    (fun (farm : Model.farm) ->
      check (farm.Model.money = 3000.0) "initial money";
      check (farm.Model.farmer_x = 4 && farm.Model.farmer_y = 4) "initial farmer position";
      check (farm.Model.hands = [||]) "initial hands";
      check (farm.Model.unlocked_quadrants = 1) "initially only NW unlocked";
      let empty = ref 0
      and locked = ref 0 in
      Array.iter
        (function
          | Model.Empty -> incr empty
          | Model.Locked -> incr locked
          | _ -> ())
        farm.Model.tiles;
      check (!empty = 25) "initial empty tile count";
      check (!locked = 75) "initial locked tile count")
    state.Model.farms;
  Array.iter
    (fun (p : Model.private_state) ->
      check (Array.for_all (( = ) 0) p.Model.shed) "initial shed is empty";
      check (Array.for_all (( = ) 0) p.Model.seeds) "initial seeds are empty";
      check (Array.length p.Model.inventories = 1) "one initial inventory")
    state.Model.privates;
  check
    (Array.for_all (( = ) 10_000) state.Model.market_inventory)
    "initial market inventory at I0";
  check
    (state.Model.market_prices = [| 25; 35; 60; 120; 250; 50; 160; 200; 100 |])
    "initial market prices at base"
;;

let pass_game_matches_terminal_convention () =
  let result = Model.run_game Model.default_config in
  check (result.Model.result_transitions = 719) "default terminal transition count";
  check (result.Model.final_money.(0) = 3000.0) "player zero pass reward";
  check (result.Model.final_money.(1) = 3000.0) "player one pass reward"
;;

let rejected_configuration_is_explicit () =
  let rejects config message =
    let rejected =
      match Model.initial_state config with
      | (_ : Model.state) -> false
      | exception Invalid_argument _ -> true
    in
    check rejected message
  in
  rejects
    { Model.default_config with Model.episode_steps = 1 }
    "episodeSteps below 2 is rejected";
  rejects
    { Model.default_config with Model.board_size = 3 }
    "boardSize below the specification minimum is rejected"
;;

let copy_is_deep () =
  let state = Model.initial_state Model.default_config in
  let clone = Model.copy state in
  clone.Model.farms.(0).Model.money <- 0.0;
  clone.Model.farms.(0).Model.tiles.(0) <- Model.Locked;
  clone.Model.privates.(0).Model.shed.(0) <- 7;
  clone.Model.privates.(0).Model.inventories.(0).Model.counts.(0) <- 7;
  clone.Model.market_inventory.(0) <- 0;
  check
    (state.Model.farms.(0).Model.money = 3000.0
     && state.Model.farms.(0).Model.tiles.(0) = Model.Empty)
    "copy does not alias farm state";
  check
    (state.Model.privates.(0).Model.shed.(0) = 0
     && state.Model.privates.(0).Model.inventories.(0).Model.counts.(0) = 0)
    "copy does not alias private state";
  check (state.Model.market_inventory.(0) = 10_000) "copy does not alias the market"
;;

let hire_costs_and_spawns_match_reference () =
  (* fib-indexed costs 1,1,2,3 and NWSE spawn preference with occupancy tie-breaking:
     farmer sits on (4,4), so hires fill (5,4), (4,5), (5,5), then wrap to (4,4). *)
  let state = Model.initial_state Model.default_config in
  let hire () =
    Model.step
      state
      { Model.pass_action with Model.market = [| Model.Hire |] }
      Model.pass_action
  in
  hire ();
  hire ();
  hire ();
  hire ();
  let farm = state.Model.farms.(0) in
  check (farm.Model.money = 3000.0 -. 7.0) "four hires cost fib 1+1+2+3";
  check (farm.Model.hires_today = 4) "hires_today counts the day's hires";
  check
    (farm.Model.hands = [| 5, 4; 4, 5; 5, 5; 4, 4 |])
    "hand spawn order is NWSE with occupancy tie-break";
  check
    (Array.length state.Model.privates.(0).Model.inventories = 5)
    "each hire adds an inventory";
  check (state.Model.farms.(1).Model.money = 3000.0) "opponent unaffected by hires"
;;

(* ---------------- rule group 1 vs the oracle ---------------- *)

let member = Yojson.Safe.Util.member
let to_int json = Yojson.Safe.Util.to_int json
let to_string json = Yojson.Safe.Util.to_string json
let to_list json = Yojson.Safe.Util.to_list json

let to_number json =
  match json with
  | `Int value -> float_of_int value
  | `Float value -> value
  | _ -> failwith "expected a JSON number"
;;

let config_of_case case =
  Kag_serialize.config_of_json
    ~seed:(to_int (member "seed" case))
    (member "configuration" case)
;;

let check_json label expected actual =
  match Kag_serialize.first_diff expected actual with
  | None -> check true label
  | Some diff -> check false (Printf.sprintf "%s (first diff at %s)" label diff)
;;

let group1_case_matches_oracle case =
  let name = to_string (member "name" case) in
  let label suffix = Printf.sprintf "group1 %s: %s" name suffix in
  let state = Model.initial_state (config_of_case case) in
  check
    (state.Model.resolved_seed = to_int (member "resolved_seed" case))
    (label "resolved seed");
  check_json
    (label "initial diagnostic state")
    (member "initial_diagnostic" case)
    (Kag_serialize.diagnostic state);
  List.iteri
    (fun player expected ->
      check_json
        (label (Printf.sprintf "initial observation player %d" player))
        expected
        (Kag_serialize.observation state ~player))
    (to_list (member "initial_observations" case));
  let turns = member "turns" case in
  let days = Array.of_list (List.map to_int (to_list (member "day" turns))) in
  let hours = Array.of_list (List.map to_int (to_list (member "hour" turns))) in
  let steps = Array.of_list (List.map to_int (to_list (member "step" turns))) in
  let final = member "final" case in
  let final_turn = to_int (member "turn" final) in
  check (Array.length days = final_turn + 1) (label "fixture covers every turn");
  let scalars_diverged = ref None in
  for turn = 0 to final_turn do
    Model.step state Model.pass_action Model.pass_action;
    let obs = Model.observe state ~player:0 in
    let expected_status = if turn = final_turn then Model.Done else Model.Active in
    if !scalars_diverged = None
       && not
            (state.Model.day = days.(turn)
             && state.Model.hour = hours.(turn)
             && obs.Model.obs_step = steps.(turn)
             && state.Model.status = expected_status
             && (turn = final_turn || Model.reward state ~player:0 = 0.0))
    then scalars_diverged := Some turn
  done;
  (match !scalars_diverged with
   | None -> check true (label "per-turn day/hour/step/status/reward")
   | Some turn ->
     check false (label (Printf.sprintf "per-turn scalars diverged at turn %d" turn)));
  check (state.Model.status = Model.Done) (label "terminal status");
  List.iteri
    (fun player expected ->
      check
        (Model.reward state ~player = to_number expected)
        (label (Printf.sprintf "terminal reward player %d" player)))
    (to_list (member "rewards" final));
  let stepping_done_rejected =
    match Model.step state Model.pass_action Model.pass_action with
    | () -> false
    | exception Invalid_argument _ -> true
  in
  check stepping_done_rejected (label "stepping a completed game is rejected")
;;

(* ---------------- action-tape rule groups vs the oracle ---------------- *)

let without_market_and_town json =
  match json with
  | `Assoc fields ->
    `Assoc (List.filter (fun (key, _) -> key <> "market" && key <> "town") fields)
  | other -> other
;;

(* Groups 2+ share the fixture shape: a recorded action tape, a per-turn digest of every
   field their rules can mutate, and the final diagnostic state. Which state enters the
   comparison is declared by the fixture itself: digests carry a "market" key (and the
   final diagnostic carries market/town) only once the market rule group is in scope for
   that fixture. *)
let json_has_key key json =
  match json with
  | `Assoc fields -> List.mem_assoc key fields
  | _ -> false
;;

let tape_case_matches_oracle ~group case =
  let name = to_string (member "name" case) in
  let label suffix = Printf.sprintf "%s %s: %s" group name suffix in
  let state = Model.initial_state (config_of_case case) in
  let tape = Array.of_list (to_list (member "tape" case)) in
  let digests = Array.of_list (to_list (member "digests" case)) in
  check (Array.length tape = Array.length digests) (label "tape and digests align");
  check
    (Array.length tape = state.Model.config.Model.episode_steps - 1)
    (label "tape covers the whole episode");
  let with_market = Array.length digests > 0 && json_has_key "market" digests.(0) in
  let with_town = Array.length digests > 0 && json_has_key "town" digests.(0) in
  let model_digest () =
    match Kag_serialize.turn_digest state with
    | `Assoc fields ->
      let fields =
        if with_market
        then ("market", Kag_serialize.json_of_market state) :: fields
        else fields
      in
      let fields =
        if with_town then ("town", Kag_serialize.json_of_town state) :: fields else fields
      in
      `Assoc fields
    | digest -> digest
  in
  let diverged = ref None in
  Array.iteri
    (fun turn actions ->
      if !diverged = None
      then (
        match to_list actions with
        | [ action0; action1 ] ->
          Model.step
            state
            (Kag_serialize.player_action_of_json action0)
            (Kag_serialize.player_action_of_json action1);
          (match Kag_serialize.first_diff digests.(turn) (model_digest ()) with
           | None -> ()
           | Some diff -> diverged := Some (Printf.sprintf "turn %d: %s" turn diff))
        | _ -> diverged := Some (Printf.sprintf "turn %d: malformed tape entry" turn)))
    tape;
  (match !diverged with
   | None -> check true (label "per-turn digests")
   | Some diff -> check false (label ("per-turn digests diverged at " ^ diff)));
  check (state.Model.status = Model.Done) (label "terminal status");
  let final = member "final_diagnostic" case in
  let model_final =
    if json_has_key "market" final
    then Kag_serialize.diagnostic state
    else without_market_and_town (Kag_serialize.diagnostic state)
  in
  check_json (label "final diagnostic state") final model_final
;;

(* ---------------- python_int vs CPython ---------------- *)

(* Upstream applies int() to raw tape values in two places whose consequences differ —
   _parse_order catches its exceptions, _apply_unit_action does not — so every
   disagreement between this and CPython is a differential divergence. *)
let python_int_matches_cpython entry =
  let text = to_string (member "json" entry) in
  let label suffix = Printf.sprintf "python_int %s: %s" text suffix in
  let actual = Kag_serialize.python_int (Yojson.Safe.from_string text) in
  match member "value" entry, member "saturates" entry, member "raises" entry with
  | `Int expected, _, _ -> check (actual = Some expected) (label "value")
  | _, `String "max", _ -> check (actual = Some max_int) (label "saturates high")
  | _, `String "min", _ -> check (actual = Some min_int) (label "saturates low")
  | _, _, `Bool true -> check (actual = None) (label "raises")
  | _ -> check false (label "malformed fixture entry")
;;

(* ---------------- Python_random vs CPython ---------------- *)

let ints_of json = List.map Yojson.Safe.Util.to_int (Yojson.Safe.Util.to_list json)

let hex_floats_of json =
  List.map
    (fun value -> float_of_string (Yojson.Safe.Util.to_string value))
    (Yojson.Safe.Util.to_list json)
;;

let rng_matches_cpython entry =
  let open Yojson.Safe.Util in
  let seed = entry |> member "seed" |> to_int in
  let label suffix = Printf.sprintf "seed %d: %s" seed suffix in
  let rng = Python_random.create seed in
  List.iter
    (fun expected -> check (Python_random.random rng = expected) (label "random()"))
    (hex_floats_of (member "random_hex" entry));
  let rng = Python_random.create seed in
  List.iter
    (fun expected ->
      check (Python_random.getrandbits rng 32 = expected) (label "getrandbits(32)"))
    (ints_of (member "getrandbits32" entry));
  let rng = Python_random.create seed in
  let range7 = Array.init 7 Fun.id in
  List.iter
    (fun expected ->
      check (Python_random.choice rng range7 = expected) (label "choice(range(7))"))
    (ints_of (member "choice7" entry));
  (* One generator serving interleaved draw kinds, the way the environment's daily block
     actually uses it. *)
  let mixed = member "mixed" entry in
  let rng = Python_random.create seed in
  List.iter
    (fun expected -> check (Python_random.random rng = expected) (label "mixed head"))
    (hex_floats_of (member "head_hex" mixed));
  check
    (Python_random.choice rng (Array.init 6 Fun.id) = (mixed |> member "choice6" |> to_int)
    )
    (label "mixed choice");
  List.iter
    (fun expected -> check (Python_random.random rng = expected) (label "mixed tail"))
    (hex_floats_of (member "tail_hex" mixed))
;;

let () =
  initialization_matches_reference ();
  pass_game_matches_terminal_convention ();
  rejected_configuration_is_explicit ();
  copy_is_deep ();
  hire_costs_and_spawns_match_reference ();
  let model_fixture = Yojson.Safe.from_file "model_group1_fixture.json" in
  List.iter group1_case_matches_oracle (Yojson.Safe.Util.to_list model_fixture);
  let group2_fixture = Yojson.Safe.from_file "model_group2_fixture.json" in
  List.iter
    (tape_case_matches_oracle ~group:"group2")
    (Yojson.Safe.Util.to_list group2_fixture);
  let group3_fixture = Yojson.Safe.from_file "model_group3_fixture.json" in
  List.iter
    (tape_case_matches_oracle ~group:"group3")
    (Yojson.Safe.Util.to_list group3_fixture);
  let group4_fixture = Yojson.Safe.from_file "model_group4_fixture.json" in
  List.iter
    (tape_case_matches_oracle ~group:"group4")
    (Yojson.Safe.Util.to_list group4_fixture);
  let group5_fixture = Yojson.Safe.from_file "model_group5_fixture.json" in
  List.iter
    (tape_case_matches_oracle ~group:"group5")
    (Yojson.Safe.Util.to_list group5_fixture);
  let group6_fixture = Yojson.Safe.from_file "model_group6_fixture.json" in
  List.iter
    (tape_case_matches_oracle ~group:"group6")
    (Yojson.Safe.Util.to_list group6_fixture);
  let group7_fixture = Yojson.Safe.from_file "model_group7_fixture.json" in
  List.iter
    (tape_case_matches_oracle ~group:"group7")
    (Yojson.Safe.Util.to_list group7_fixture);
  let int_fixture = Yojson.Safe.from_file "python_int_fixture.json" in
  List.iter python_int_matches_cpython (Yojson.Safe.Util.to_list int_fixture);
  let fixture = Yojson.Safe.from_file "python_random_fixture.json" in
  List.iter rng_matches_cpython (Yojson.Safe.Util.to_list fixture);
  if !failures > 0
  then (
    Printf.printf "%d failure(s)\n" !failures;
    exit 1)
  else print_endline "all fast_model tests passed"
;;
