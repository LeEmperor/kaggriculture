(* kag-sim: the simulator CLI. Same contract as the retired C++ binary:

     kag_sim.exe bench [--games N]

   The bench drives PASS tapes through the full rule set — not a policy
   workload; see docs/benchmark_baseline.md before quoting it anywhere. *)

let parse_games argv =
  let games = ref 100_000 in
  let i = ref 2 in
  let argc = Array.length argv in
  while !i < argc do
    (match argv.(!i) with
     | "--games" when !i + 1 < argc ->
       incr i;
       (match int_of_string_opt argv.(!i) with
        | Some value when value > 0 -> games := value
        | _ ->
          prerr_endline "--games expects a positive integer";
          exit 2)
     | arg ->
       Printf.eprintf "unknown argument: %s\n" arg;
       exit 2);
    incr i
  done;
  !games

let benchmark argv =
  let games = parse_games argv in
  let transitions = ref 0 in
  let checksum = ref 0.0 in
  let started = Unix.gettimeofday () in
  for game = 0 to games - 1 do
    let state = Kag_model.Model.initial_state Kag_model.Model.default_config in
    while state.Kag_model.Model.status = Kag_model.Model.Active do
      Kag_model.Model.step state Kag_model.Model.pass_action Kag_model.Model.pass_action;
      incr transitions
    done;
    checksum
    := !checksum
       +. state.Kag_model.Model.farms.(game mod Kag_model.Model.player_count)
            .Kag_model.Model.money
  done;
  let seconds = Unix.gettimeofday () -. started in
  Printf.printf
    "backend=ocaml-scalar-pass-tape\n\
     games=%d\n\
     transitions=%d\n\
     seconds=%.3f\n\
     games_per_second=%.3f\n\
     transitions_per_second=%.3f\n\
     nanoseconds_per_transition=%.3f\n\
     checksum=%.3f\n"
    games
    !transitions
    seconds
    (float_of_int games /. seconds)
    (float_of_int !transitions /. seconds)
    (seconds *. 1.0e9 /. float_of_int !transitions)
    !checksum

let () =
  if Array.length Sys.argv >= 2 && Sys.argv.(1) = "bench"
  then benchmark Sys.argv
  else (
    Printf.eprintf "usage: %s bench [--games N]\n" Sys.argv.(0);
    exit 2)
