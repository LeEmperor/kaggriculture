(* kag-sim: the simulator CLI.

   kag_sim.exe bench [--games N]
   kag_sim.exe differential [--bundle FILE]
   kag_sim.exe play --seed N --family FILE --policy-a FILE [--policy-b pass|FILE]
                    [--trace FILE]
   kag_sim.exe evaluate --family FILE --candidate FILE --opponents FILE --seeds FILE

   The bench drives PASS tapes through the full rule set — not a policy workload; see
   docs/benchmark_baseline.md before quoting it anywhere. [differential] is the
   verification half of the Phase 4 trust gate; drive it through
   [python3 -m tools.differential], which owns the oracle half. [play] and [evaluate]
   execute the DSL against native observations and actions: no JSON enters the turn loop. *)

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

let read_json path =
  match Yojson.Safe.from_file path with
  | json -> json
  | exception exn ->
    failwith (Printf.sprintf "cannot read %s: %s" path (Printexc.to_string exn))
;;

let json_field key = function
  | `Assoc fields -> List.assoc_opt key fields
  | _ -> None
;;

let candidate_parameters (family : Policy_dsl.Family.t) path =
  let candidate = read_json path in
  (match json_field "policy_id" candidate with
   | Some (`String id) when id = family.policy_id -> ()
   | Some (`String id) ->
     failwith
       (Printf.sprintf
          "%s targets policy_id %S but the family is %S"
          path
          id
          family.policy_id)
   | _ -> failwith (Printf.sprintf "%s has no string policy_id" path));
  (match json_field "schema_version" candidate with
   | Some (`Int 1) -> ()
   | Some version ->
     failwith
       (Printf.sprintf
          "%s has unsupported schema_version %s"
          path
          (Yojson.Safe.to_string version))
   | None -> failwith (Printf.sprintf "%s has no schema_version" path));
  match json_field "parameters" candidate with
  | Some parameters -> Policy_dsl.Family.bind family parameters
  | None -> failwith (Printf.sprintf "%s has no parameters block" path)
;;

let load_family path =
  Policy_dsl.Family.load
    (read_json path)
    ~observations:Kag_vocabulary.Native_vocabulary.t.Policy_dsl.Interpreter.kinds
    ~emits:Kag_vocabulary.Native_actions.emits
;;

type policy_factory = unit -> Model.policy

let pass_factory : policy_factory = fun () _ -> Model.pass_action

let candidate_factory family path : policy_factory =
  let interpreter =
    Policy_dsl.Interpreter.create
      ~family
      ~parameters:(candidate_parameters family path)
      ~vocabulary:Kag_vocabulary.Native_vocabulary.t
      ~build_action:Kag_vocabulary.Native_actions.build_action
  in
  fun () ->
    let policy = Policy_dsl.Interpreter.Policy.create interpreter in
    Policy_dsl.Interpreter.Policy.act policy
;;

let factory_of_spec family = function
  | "pass" -> pass_factory
  | candidate -> candidate_factory family candidate
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
  if !family_path = "" then failwith "play requires --family";
  if !policy_a = "" then failwith "play requires --policy-a";
  let family = load_family !family_path in
  let make_a = factory_of_spec family !policy_a
  and make_b = factory_of_spec family !policy_b in
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
  let result =
    Model.run_game
      ~on_actions
      { Model.default_config with seed }
      ~policy_a:(make_a ())
      ~policy_b:(make_b ())
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

(* ---------------- evaluate ---------------- *)

let read_lines path =
  let input = open_in path in
  let lines = ref [] in
  (try
     while true do
       let line = String.trim (input_line input) in
       if line <> "" && line.[0] <> '#' then lines := line :: !lines
     done
   with
   | End_of_file -> ());
  close_in input;
  List.rev !lines
;;

let evaluate argv =
  let family_path = ref ""
  and candidate_path = ref ""
  and opponents_path = ref ""
  and seeds_path = ref "" in
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
     | "--family" -> value "--family" (fun path -> family_path := path)
     | "--candidate" -> value "--candidate" (fun path -> candidate_path := path)
     | "--opponents" -> value "--opponents" (fun path -> opponents_path := path)
     | "--seeds" -> value "--seeds" (fun path -> seeds_path := path)
     | arg -> failwith ("unknown argument: " ^ arg));
    incr i
  done;
  List.iter
    (fun (name, value) -> if !value = "" then failwith ("evaluate requires " ^ name))
    [ "--family", family_path
    ; "--candidate", candidate_path
    ; "--opponents", opponents_path
    ; "--seeds", seeds_path
    ];
  let family = load_family !family_path in
  let candidate = candidate_factory family !candidate_path in
  let opponent_specs =
    match read_json !opponents_path with
    | `List values ->
      List.map
        (function
          | `String "pass" -> "pass"
          | `String path when Filename.is_relative path ->
            Filename.concat (Filename.dirname !opponents_path) path
          | `String path -> path
          | value ->
            failwith
              ("opponents must be an array of candidate paths or \"pass\", got "
               ^ Yojson.Safe.to_string value))
        values
    | _ -> failwith "opponents must be a JSON array"
  in
  if opponent_specs = [] then failwith "opponents must not be empty";
  let seeds =
    List.map
      (fun raw ->
        match int_of_string_opt raw with
        | Some seed -> seed
        | None -> failwith (Printf.sprintf "invalid seed %S in %s" raw !seeds_path))
      (read_lines !seeds_path)
  in
  if seeds = [] then failwith "seeds must not be empty";
  let games = ref 0
  and wins = ref 0
  and draws = ref 0
  and losses = ref 0
  and candidate_money = ref 0.0
  and opponent_money = ref 0.0 in
  let record candidate_value opponent_value =
    incr games;
    candidate_money := !candidate_money +. candidate_value;
    opponent_money := !opponent_money +. opponent_value;
    if candidate_value > opponent_value
    then incr wins
    else if candidate_value < opponent_value
    then incr losses
    else incr draws
  in
  List.iter
    (fun opponent_spec ->
      let opponent = factory_of_spec family opponent_spec in
      List.iter
        (fun seed ->
          let config = { Model.default_config with seed } in
          let as_a =
            Model.run_game config ~policy_a:(candidate ()) ~policy_b:(opponent ())
          in
          record as_a.final_money.(0) as_a.final_money.(1);
          let as_b =
            Model.run_game config ~policy_a:(opponent ()) ~policy_b:(candidate ())
          in
          record as_b.final_money.(1) as_b.final_money.(0))
        seeds)
    opponent_specs;
  let games_float = float_of_int !games in
  print_endline
    (Yojson.Safe.to_string
       (`Assoc
         [ "backend", `String "ocaml-native"
         ; "candidate", `String !candidate_path
         ; "opponents", `Int (List.length opponent_specs)
         ; "seeds", `Int (List.length seeds)
         ; "games", `Int !games
         ; "wins", `Int !wins
         ; "draws", `Int !draws
         ; "losses", `Int !losses
         ; "mean_candidate_money", `Float (!candidate_money /. games_float)
         ; "mean_opponent_money", `Float (!opponent_money /. games_float)
         ; "mean_margin", `Float ((!candidate_money -. !opponent_money) /. games_float)
         ]))
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
    | "evaluate" -> evaluate Sys.argv
    | "differential" -> differential Sys.argv
    | _ ->
      Printf.eprintf
        "usage: %s bench [--games N]\n\
        \       %s play --seed N --family FILE --policy-a FILE [--policy-b pass|FILE] \
         [--trace FILE]\n\
        \       %s evaluate --family FILE --candidate FILE --opponents FILE --seeds FILE\n\
        \       %s differential [--bundle FILE]\n"
        Sys.argv.(0)
        Sys.argv.(0)
        Sys.argv.(0)
        Sys.argv.(0);
      exit 2
  with
  | exn ->
    Printf.eprintf "kag_sim: %s\n" (Printexc.to_string exn);
    exit 2
;;
