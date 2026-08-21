(* Two suites: the model scaffold's reference facts (ported from the retired
   C++ tests) and Python_random against draws recorded from CPython itself
   (python_random_fixture.json — regenerate with the script documented in
   fast_model/README.md, never by hand).

   The RNG fixture stores floats as Python float.hex() strings and compares
   exactly: bit-for-bit agreement is the entire point, an approximate MT19937
   is worthless for differential replay. *)

open Kag_model

let failures = ref 0

let check condition message =
  if condition
  then Printf.printf "PASS %s\n" message
  else (
    incr failures;
    Printf.printf "FAIL %s\n" message)

(* ---------------- model scaffold ---------------- *)

let initialization_matches_reference () =
  let state = Model.initial_state Model.default_config in
  check (state.Model.status = Model.Active) "initial status";
  check (state.Model.transitions = 0) "initial transition count";
  Array.iter
    (fun farm ->
       check (farm.Model.money = 3000.0) "initial money";
       check
         (farm.Model.farmer_x = 4 && farm.Model.farmer_y = 4)
         "initial farmer position";
       let empty = ref 0
       and locked = ref 0 in
       Array.iter
         (function
           | Model.Empty -> incr empty
           | Model.Locked -> incr locked)
         farm.Model.tiles;
       check (!empty = 25) "initial empty tile count";
       check (!locked = 75) "initial locked tile count")
    state.Model.farms

let pass_game_matches_terminal_convention () =
  let result = Model.run_game Model.default_config in
  check (result.Model.result_transitions = 719) "default terminal transition count";
  check (result.Model.final_money.(0) = 3000.0) "player zero pass reward";
  check (result.Model.final_money.(1) = 3000.0) "player one pass reward"

let rejected_configuration_is_explicit () =
  let rejected =
    match Model.initial_state { Model.default_config with Model.board_size = 8 } with
    | (_ : Model.state) -> false
    | exception Invalid_argument _ -> true
  in
  check rejected "unsupported board size is rejected"

let copy_is_deep () =
  let state = Model.initial_state Model.default_config in
  let clone = Model.copy state in
  clone.Model.farms.(0).Model.money <- 0.0;
  clone.Model.farms.(0).Model.tiles.(0) <- Model.Locked;
  check
    (state.Model.farms.(0).Model.money = 3000.0
     && state.Model.farms.(0).Model.tiles.(0) = Model.Empty)
    "copy does not alias farm state"

(* ---------------- Python_random vs CPython ---------------- *)

let ints_of json = List.map Yojson.Safe.Util.to_int (Yojson.Safe.Util.to_list json)

let hex_floats_of json =
  List.map
    (fun value -> float_of_string (Yojson.Safe.Util.to_string value))
    (Yojson.Safe.Util.to_list json)

let rng_matches_cpython entry =
  let open Yojson.Safe.Util in
  let seed = entry |> member "seed" |> to_int in
  let label suffix = Printf.sprintf "seed %d: %s" seed suffix in

  let rng = Python_random.create seed in
  List.iter
    (fun expected ->
       check (Python_random.random rng = expected) (label "random()"))
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

  (* One generator serving interleaved draw kinds, the way the environment's
     daily block actually uses it. *)
  let mixed = member "mixed" entry in
  let rng = Python_random.create seed in
  List.iter
    (fun expected -> check (Python_random.random rng = expected) (label "mixed head"))
    (hex_floats_of (member "head_hex" mixed));
  check
    (Python_random.choice rng (Array.init 6 Fun.id) = (mixed |> member "choice6" |> to_int))
    (label "mixed choice");
  List.iter
    (fun expected -> check (Python_random.random rng = expected) (label "mixed tail"))
    (hex_floats_of (member "tail_hex" mixed))

let () =
  initialization_matches_reference ();
  pass_game_matches_terminal_convention ();
  rejected_configuration_is_explicit ();
  copy_is_deep ();
  let fixture = Yojson.Safe.from_file "python_random_fixture.json" in
  List.iter rng_matches_cpython (Yojson.Safe.Util.to_list fixture);
  if !failures > 0
  then (
    Printf.printf "%d failure(s)\n" !failures;
    exit 1)
  else print_endline "all fast_model tests passed"
