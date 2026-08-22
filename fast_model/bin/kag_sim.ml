(* kag-sim: the simulator CLI.

   kag_sim.exe bench [--games N]
   kag_sim.exe differential [--bundle FILE]
   kag_sim.exe play --seed N --family FILE --policy-a FILE [--policy-b pass|FILE]
                    [--trace FILE]
   kag_sim.exe evaluate (--baseline ID | --family FILE --candidate FILE)
                        --opponents FILE --seeds FILE [--threads N] [--copies N]
                        [--coverage] [--artifact FILE] [--label NAME]
   kag_sim.exe league --entrants FILE --seeds FILE [--family FILE] [--threads N]
                      [--artifact FILE] [--no-coverage]

   The bench drives PASS tapes through the full rule set — not a policy workload; see
   docs/benchmark_baseline.md before quoting it anywhere. [differential] is the
   verification half of the Phase 4 trust gate; drive it through
   [python3 -m tools.differential], which owns the oracle half. [play] and [evaluate]
   execute the DSL against native observations and actions: no JSON enters the turn loop.
   [evaluate] and [league] are the Phase 6 evaluation layer; both accept native baselines
   (kag_baselines) and DSL candidates as entrants, and the statistics they report are the
   game plan's Phase 7 list. See docs/evaluation_protocol.md. *)

module Model = Kag_model.Model

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
;;

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
;;

(* ---------------- native policy loading ---------------- *)

(* The family/candidate readers and the entrant vocabulary live in [Evaluation], which
   [Runner] also needs; [play] uses the same resolver so a baseline and a DSL candidate
   are interchangeable on either side of a game. *)

let factory_of_spec ~family spec : Model.config -> seat:int -> Model.policy =
  let entrant =
    Evaluation.of_spec
      ~base_dir:(Filename.dirname spec)
      ~ambient_family:family
      (`String spec)
  in
  entrant.Evaluation.create
;;

let json_of_result ~seed (result : Model.result) : Yojson.Safe.t =
  `Assoc
    [ "backend", `String "ocaml-native"
    ; "seed", `Int seed
    ; "turns", `Int result.result_transitions
    ; ( "final_money"
      , `List (List.map (fun x -> `Float x) (Array.to_list result.final_money)) )
    ]
;;

(* ---------------- play ---------------- *)

let play argv =
  let seed = ref None
  and family_path = ref ""
  and policy_a = ref ""
  and policy_b = ref "pass"
  and trace_path = ref None in
  let i = ref 2 in
  while !i < Array.length argv do
    let value option assign =
      if !i + 1 >= Array.length argv
      then failwith (option ^ " expects a value")
      else (
        incr i;
        assign argv.(!i))
    in
    (match argv.(!i) with
     | "--seed" ->
       value "--seed" (fun raw ->
         match int_of_string_opt raw with
         | Some value -> seed := Some value
         | None -> failwith "--seed expects an integer")
     | "--family" -> value "--family" (fun path -> family_path := path)
     | "--policy-a" -> value "--policy-a" (fun path -> policy_a := path)
     | "--policy-b" -> value "--policy-b" (fun path -> policy_b := path)
     | "--trace" -> value "--trace" (fun path -> trace_path := Some path)
     | arg -> failwith ("unknown argument: " ^ arg));
    incr i
  done;
  let seed =
    match !seed with
    | Some seed -> seed
    | None -> failwith "play requires --seed"
  in
  if !policy_a = "" then failwith "play requires --policy-a";
  let family = if !family_path = "" then None else Some !family_path in
  let make_a = factory_of_spec ~family !policy_a
  and make_b = factory_of_spec ~family !policy_b in
  let records = ref [] in
  let on_actions ~turn action_a action_b =
    records
    := `Assoc
         [ "seed", `Int seed
         ; "turn", `Int turn
         ; ( "actions"
           , `List
               [ Kag_serialize.json_of_player_action action_a
               ; Kag_serialize.json_of_player_action action_b
               ] )
         ]
       :: !records
  in
  let config = { Model.default_config with seed } in
  let result =
    Model.run_game
      ~on_actions
      config
      ~policy_a:(make_a config ~seat:0)
      ~policy_b:(make_b config ~seat:1)
  in
  (match !trace_path with
   | None -> ()
   | Some path ->
     let output = open_out path in
     List.iter
       (fun record ->
         output_string output (Yojson.Safe.to_string record);
         output_char output '\n')
       (List.rev !records);
     close_out output);
  let summary : Yojson.Safe.t =
    let result_json = json_of_result ~seed result in
    match !trace_path with
    | None -> result_json
    | Some path ->
      (match result_json with
       | `Assoc fields -> `Assoc (("trace", `String path) :: fields)
       | other -> other)
  in
  print_endline (Yojson.Safe.to_string summary)
;;

(* ---------------- differential ----------------

   kag_sim.exe differential [--bundle FILE]

   The verification half of the Phase 4 trust gate. Reads one JSON object per line —
   a game recorded from the pinned oracle by [python3 -m tools.differential] — replays
   its raw action tape through the engine, and compares every post-turn state. One
   verdict object per line goes back on stdout, so the two halves stream against each
   other and a 1,000-game gate never lands a bundle on disk.

   The tape stays raw JSON on both sides: reproducing upstream's collapse of malformed
   input is [Kag_serialize.player_action_of_json_tolerant]'s job, and is itself under
   test here. Anything in the domain upstream leaves undefined raises rather than
   guessing, and is reported as its own verdict stage. *)

let member = Yojson.Safe.Util.member
let to_int json = Yojson.Safe.Util.to_int json
let to_list json = Yojson.Safe.Util.to_list json

(* Every field the engine can mutate, plus the clock — the widest per-turn scope the
   engine can express. [Kag_serialize.turn_digest] projects tiles sparsely; absent tiles
   are Empty or Locked, and which is which is fixed by [unlocked_quadrants], which the
   digest carries. *)
let differential_digest (state : Model.state) : Yojson.Safe.t =
  match Kag_serialize.turn_digest state with
  | `Assoc fields ->
    `Assoc
      (fields
       @ [ "market", Kag_serialize.json_of_market state
         ; "town", Kag_serialize.json_of_town state
         ; "day", `Int state.Model.day
         ; "hour", `Int state.Model.hour
         ])
  | other -> other
;;

let verdict fields = `Assoc fields

let diverged ~stage ~turn ~path ~native =
  verdict
    [ "ok", `Bool false
    ; "stage", `String stage
    ; "turn", `Int turn
    ; "path", `String path
    ; "native", native
    ]
;;

let replay_game record =
  let seed = to_int (member "seed" record) in
  let config = Kag_serialize.config_of_json ~seed (member "configuration" record) in
  let tape = Array.of_list (to_list (member "tape" record)) in
  let digests = Array.of_list (to_list (member "digests" record)) in
  (* A minimized reproducer stops at the diverging turn, so only a complete episode is
     held to the full length and to the terminal comparisons. Absent means complete: a
     recorded game always is. *)
  let complete = member "complete" record <> `Bool false in
  if Array.length tape <> Array.length digests
  then
    diverged
      ~stage:"bundle"
      ~turn:(-1)
      ~path:
        (Printf.sprintf
           "tape and digests disagree (%d vs %d)"
           (Array.length tape)
           (Array.length digests))
      ~native:`Null
  else if complete && Array.length tape <> config.Model.episode_steps - 1
  then
    diverged
      ~stage:"bundle"
      ~turn:(-1)
      ~path:
        (Printf.sprintf
           "tape covers %d turns, episodeSteps implies %d"
           (Array.length tape)
           (config.Model.episode_steps - 1))
      ~native:`Null
  else (
    let state = Model.initial_state config in
    let result = ref None in
    let turn = ref 0 in
    while !result = None && !turn < Array.length tape do
      let index = !turn in
      (match to_list tape.(index) with
       | [ action0; action1 ] ->
         Model.step
           state
           (Kag_serialize.player_action_of_json_tolerant action0)
           (Kag_serialize.player_action_of_json_tolerant action1);
         let native = differential_digest state in
         (match Kag_serialize.first_diff digests.(index) native with
          | None -> ()
          | Some path ->
            result := Some (diverged ~stage:"digest" ~turn:index ~path ~native))
       | _ ->
         result
         := Some
              (diverged
                 ~stage:"bundle"
                 ~turn:index
                 ~path:"tape entry is not a pair of actions"
                 ~native:`Null));
      incr turn
    done;
    match !result with
    | Some failure -> failure
    | None when not complete ->
      verdict [ "ok", `Bool true; "turns", `Int (Array.length tape) ]
    | None ->
      let final_turn = Array.length tape - 1 in
      if state.Model.status <> Model.Done
      then
        diverged
          ~stage:"terminal"
          ~turn:final_turn
          ~path:"engine is still ACTIVE after the last recorded turn"
          ~native:`Null
      else (
        let native_final = Kag_serialize.diagnostic state in
        match
          Kag_serialize.first_diff (member "final_diagnostic" record) native_final
        with
        | Some path -> diverged ~stage:"final" ~turn:final_turn ~path ~native:native_final
        | None ->
          let native_reward =
            `List
              (List.init Model.player_count (fun player ->
                 `Float (Model.reward state ~player)))
          in
          let native_status =
            `List (List.init Model.player_count (fun _ -> `String "DONE"))
          in
          (match
             Kag_serialize.first_diff (member "final_status" record) native_status
           with
           | Some path ->
             diverged ~stage:"status" ~turn:final_turn ~path ~native:native_status
           | None ->
             (match
                Kag_serialize.first_diff (member "final_reward" record) native_reward
              with
              | Some path ->
                diverged ~stage:"reward" ~turn:final_turn ~path ~native:native_reward
              | None -> verdict [ "ok", `Bool true; "turns", `Int (Array.length tape) ]))))
;;

let differential argv =
  let bundle = ref "-" in
  let i = ref 2 in
  let argc = Array.length argv in
  while !i < argc do
    (match argv.(!i) with
     | "--bundle" when !i + 1 < argc ->
       incr i;
       bundle := argv.(!i)
     | arg ->
       Printf.eprintf "unknown argument: %s\n" arg;
       exit 2);
    incr i
  done;
  let input = if !bundle = "-" then stdin else open_in !bundle in
  let failures = ref 0 in
  let games = ref 0 in
  (try
     while true do
       let line = input_line input in
       if String.trim line <> ""
       then (
         let record = Yojson.Safe.from_string line in
         let index =
           try to_int (member "index" record) with
           | _ -> !games
         in
         let outcome =
           try replay_game record with
           | Kag_serialize.Undefined_mapping message ->
             diverged ~stage:"undefined" ~turn:(-1) ~path:message ~native:`Null
           | exn ->
             diverged
               ~stage:"exception"
               ~turn:(-1)
               ~path:(Printexc.to_string exn)
               ~native:`Null
         in
         let fields =
           match outcome with
           | `Assoc fields ->
             ("index", `Int index) :: ("seed", member "seed" record) :: fields
           | other -> [ "index", `Int index; "outcome", other ]
         in
         if member "ok" outcome <> `Bool true then incr failures;
         incr games;
         print_string (Yojson.Safe.to_string (`Assoc fields));
         print_newline ();
         flush stdout)
     done
   with
   | End_of_file -> ());
  if input != stdin then close_in input;
  Printf.eprintf "differential: %d games, %d divergent\n" !games !failures;
  if !failures > 0 then exit 1
;;

let () =
  try
    match if Array.length Sys.argv >= 2 then Sys.argv.(1) else "" with
    | "bench" -> benchmark Sys.argv
    | "play" -> play Sys.argv
    | "evaluate" -> Runner.evaluate Sys.argv
    | "league" -> Runner.league Sys.argv
    | "differential" -> differential Sys.argv
    | _ ->
      Printf.eprintf
        "usage: %s bench [--games N]\n\
        \       %s play --seed N [--family FILE] --policy-a SPEC [--policy-b SPEC] \
         [--trace FILE]\n\
        \       %s evaluate (--baseline ID | --family FILE --candidate FILE) \
         --opponents FILE --seeds FILE [--threads N] [--copies N] [--coverage] \
         [--artifact FILE] [--label NAME]\n\
        \       %s league --entrants FILE --seeds FILE [--family FILE] [--threads N] \
         [--artifact FILE] [--label NAME] [--no-coverage]\n\
        \       %s differential [--bundle FILE]\n\
         \n\
         SPEC is \"pass\", \"baseline:<id>\", or a candidate JSON path (needs --family).\n\
         Baselines: %s\n"
        Sys.argv.(0)
        Sys.argv.(0)
        Sys.argv.(0)
        Sys.argv.(0)
        Sys.argv.(0)
        (String.concat " " Kag_baselines.Registry.ids);
      exit 2
  with
  | exn ->
    Printf.eprintf "kag_sim: %s\n" (Printexc.to_string exn);
    exit 2
;;
