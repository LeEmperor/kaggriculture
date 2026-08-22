(* The evaluation drivers: [evaluate] measures one entrant against a population, and
   [league] plays a population against itself.

   Both are the same machine. A job is an ordered pair of entrants plus a seed; every
   pair is played in both seat orders on every seed, which is the game plan's "evaluate
   both player positions and use common random numbers" — the same seeds and the same
   opponents for everyone, so a difference between two entrants is a difference between
   the entrants.

   Results are written into a preallocated slot per job rather than accumulated, because
   medians, percentiles, and intervals are not accumulable and because a slot per job
   makes the parallel path trivially race-free: workers touch disjoint indices and every
   statistic is computed on one domain afterwards. *)

module Model = Kag_model.Model
module Coverage = Kag_baselines.Coverage

type job =
  { seat_a : int (* entrant index playing player 0 *)
  ; seat_b : int (* entrant index playing player 1 *)
  ; seed : int
  }

type outcome =
  { money_a : float
  ; money_b : float
  ; transitions : int
  }

let run_jobs ~threads ~(entrants : Evaluation.entrant array) ~jobs ~outcomes ~coverage =
  let next_job = Atomic.make 0 in
  let worker () =
    let rec loop () =
      let index = Atomic.fetch_and_add next_job 1 in
      if index < Array.length jobs
      then (
        let job = jobs.(index) in
        let config = { Model.default_config with seed = job.seed } in
        let policy_a = entrants.(job.seat_a).create config ~seat:0 in
        let policy_b = entrants.(job.seat_b).create config ~seat:1 in
        let result =
          match coverage with
          | None -> Model.run_game config ~policy_a ~policy_b
          | Some slots ->
            let for_a = Coverage.create ()
            and for_b = Coverage.create () in
            slots.(index) <- Some (for_a, for_b);
            let on_actions ~turn:_ action_a action_b =
              Coverage.observe for_a action_a;
              Coverage.observe for_b action_b
            in
            Model.run_game ~on_actions config ~policy_a ~policy_b
        in
        outcomes.(index)
        <- Some
             { money_a = result.final_money.(0)
             ; money_b = result.final_money.(1)
             ; transitions = result.result_transitions
             };
        loop ())
    in
    loop ()
  in
  let workers = max 1 (min threads (Array.length jobs)) in
  if workers <= 1
  then worker ()
  else (
    (* Same ownership argument as the Phase 5 pool: this executable runs no other
       threading model, the captured interpreters are immutable, and every mutable value
       a worker touches is either the atomic cursor or a slot no other worker indexes. *)
    let spawn = Domain.spawn [@alert "-do_not_spawn_domains-unsafe_multidomain"] in
    let domains = Array.init (workers - 1) (fun _ -> spawn worker) in
    worker ();
    Array.iter Domain.join domains)
;;

let outcome_at outcomes index =
  match outcomes.(index) with
  | Some outcome -> outcome
  | None -> failwith "internal: a job produced no result"
;;

(* One entrant's games as seen from its own side, whichever seat it sat in. *)
let games_for ~jobs ~outcomes ~entrant ~opponent_of =
  let games = ref [] in
  Array.iteri
    (fun index (job : job) ->
      let outcome = outcome_at outcomes index in
      if job.seat_a = entrant
      then
        games
        := { Evaluation.opponent = opponent_of job.seat_b
           ; seat = 0
           ; seed = job.seed
           ; money = outcome.money_a
           ; opponent_money = outcome.money_b
           ; transitions = outcome.transitions
           }
           :: !games
      else if job.seat_b = entrant
      then
        games
        := { Evaluation.opponent = opponent_of job.seat_a
           ; seat = 1
           ; seed = job.seed
           ; money = outcome.money_b
           ; opponent_money = outcome.money_a
           ; transitions = outcome.transitions
           }
           :: !games)
    jobs;
  List.rev !games
;;

let merge_coverage ~jobs ~coverage ~entrant =
  let total = Coverage.create () in
  (match coverage with
   | None -> ()
   | Some slots ->
     Array.iteri
       (fun index (job : job) ->
         match slots.(index) with
         | None -> ()
         | Some (for_a, for_b) ->
           if job.seat_a = entrant then Coverage.merge total for_a;
           if job.seat_b = entrant then Coverage.merge total for_b)
       jobs);
  total
;;

(* ---------------- shared argument plumbing ---------------- *)

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

let read_seeds path =
  let seeds =
    List.map
      (fun raw ->
        match int_of_string_opt raw with
        | Some seed -> seed
        | None -> failwith (Printf.sprintf "invalid seed %S in %s" raw path))
      (read_lines path)
  in
  if seeds = [] then failwith (path ^ " lists no seeds");
  seeds
;;

let parse_options argv ~options =
  let index = ref 2 in
  while !index < Array.length argv do
    let name = argv.(!index) in
    (match List.assoc_opt name options with
     | Some assign ->
       if !index + 1 >= Array.length argv
       then failwith (name ^ " expects a value")
       else (
         incr index;
         assign argv.(!index))
     | None ->
       (match List.assoc_opt (name ^ "!flag") options with
        | Some assign -> assign ""
        | None -> failwith ("unknown argument: " ^ name)));
    incr index
  done
;;

let positive_int name assign raw =
  match int_of_string_opt raw with
  | Some value when value > 0 -> assign value
  | _ -> failwith (name ^ " expects a positive integer")
;;

let write_artifact path json =
  let channel = open_out path in
  output_string channel (Yojson.Safe.pretty_to_string json);
  output_char channel '\n';
  close_out channel
;;

let timing ~started ~cpu_started ~transitions =
  let seconds = Unix.gettimeofday () -. started in
  let cpu_finished = Unix.times () in
  let cpu_seconds =
    cpu_finished.Unix.tms_utime
    +. cpu_finished.Unix.tms_stime
    -. cpu_started.Unix.tms_utime
    -. cpu_started.Unix.tms_stime
  in
  ( seconds
  , cpu_seconds
  , `Assoc
      [ "wall_seconds", `Float seconds
      ; "cpu_seconds", `Float cpu_seconds
      ; "cpu_utilization_percent", `Float (cpu_seconds /. seconds *. 100.0)
      ; "turns_per_second", `Float (float_of_int transitions /. seconds)
      ] )
;;

(* Coverage is a gate, not a report: a declared action shape that never appeared means
   the declaration and the policy disagree. *)
let coverage_report ~(entrant : Evaluation.entrant) coverage =
  let missing =
    Coverage.missing coverage ~expect_ops:entrant.expect_ops ~expect_orders:entrant.expect_orders
  in
  ( missing
  , `Assoc
      [ "declared_shapes_missing", `List (List.map (fun name -> `String name) missing)
      ; "observed", Evaluation.json_of_coverage coverage
      ] )
;;

(* ---------------- evaluate ---------------- *)

let evaluate argv =
  let family = ref ""
  and candidate = ref ""
  and baseline = ref ""
  and opponents_path = ref ""
  and seeds_path = ref ""
  and threads = ref 1
  and copies = ref 1
  and label = ref ""
  and artifact = ref ""
  and want_coverage = ref false in
  parse_options
    argv
    ~options:
      [ "--family", (fun value -> family := value)
      ; "--candidate", (fun value -> candidate := value)
      ; "--baseline", (fun value -> baseline := value)
      ; "--opponents", (fun value -> opponents_path := value)
      ; "--seeds", (fun value -> seeds_path := value)
      ; "--label", (fun value -> label := value)
      ; "--artifact", (fun value -> artifact := value)
      ; "--threads", positive_int "--threads" (fun value -> threads := value)
      ; "--copies", positive_int "--copies" (fun value -> copies := value)
      ; "--coverage!flag", (fun _ -> want_coverage := true)
      ];
  if !opponents_path = "" then failwith "evaluate requires --opponents";
  if !seeds_path = "" then failwith "evaluate requires --seeds";
  if (!candidate = "") = (!baseline = "")
  then failwith "evaluate requires exactly one of --candidate or --baseline";
  let ambient_family = if !family = "" then None else Some !family in
  let measured =
    if !baseline <> ""
    then Evaluation.baseline_entrant !baseline
    else (
      match ambient_family with
      | Some family_path -> Evaluation.dsl_entrant ~family_path ~candidate_path:!candidate
      | None -> failwith "--candidate requires --family")
  in
  let opponents = Array.of_list (Evaluation.of_file ~ambient_family !opponents_path) in
  let seeds = read_seeds !seeds_path in
  let entrants = Array.append [| measured |] opponents in
  (* Entrant 0 is the measured policy; opponent i is entrant i + 1. Both seat orders of
     every (measured, opponent, seed) triple, so position is controlled rather than
     averaged over by luck. *)
  let jobs =
    Array.of_list
      (List.concat_map
         (fun _copy ->
           List.concat_map
             (fun opponent ->
               List.concat_map
                 (fun seed ->
                   [ { seat_a = 0; seat_b = opponent + 1; seed }
                   ; { seat_a = opponent + 1; seat_b = 0; seed }
                   ])
                 seeds)
             (List.init (Array.length opponents) Fun.id))
         (List.init !copies Fun.id))
  in
  let outcomes = Array.make (Array.length jobs) None in
  let coverage =
    if !want_coverage then Some (Array.make (Array.length jobs) None) else None
  in
  let cpu_started = Unix.times () in
  let started = Unix.gettimeofday () in
  run_jobs ~threads:!threads ~entrants ~jobs ~outcomes ~coverage;
  let games =
    games_for ~jobs ~outcomes ~entrant:0 ~opponent_of:(fun entrant -> entrant - 1)
  in
  let config = Model.default_config in
  let overall = Evaluation.summarize ~config games in
  let seconds, _cpu, timing_json = timing ~started ~cpu_started ~transitions:overall.transitions in
  let per_opponent =
    List.init (Array.length opponents) (fun index ->
      let subset = List.filter (fun (g : Evaluation.game) -> g.opponent = index) games in
      opponents.(index).label, Evaluation.summarize ~config subset)
  in
  let worst =
    List.fold_left
      (fun worst (name, (summary : Evaluation.summary)) ->
        match worst with
        | Some (_, best) when best <= summary.score_rate -> worst
        | _ -> Some (name, summary.score_rate))
      None
      per_opponent
  in
  let by_seat =
    List.map
      (fun seat ->
        let subset = List.filter (fun (g : Evaluation.game) -> g.seat = seat) games in
        (if seat = 0 then "player_0" else "player_1"), Evaluation.summarize ~config subset)
      [ 0; 1 ]
  in
  let coverage_total = merge_coverage ~jobs ~coverage ~entrant:0 in
  let missing, coverage_json = coverage_report ~entrant:measured coverage_total in
  let candidate_total =
    List.fold_left (fun acc (g : Evaluation.game) -> acc +. g.money) 0.0 games
  and opponent_total =
    List.fold_left (fun acc (g : Evaluation.game) -> acc +. g.opponent_money) 0.0 games
  in
  let games_float = float_of_int overall.games in
  let summary_json : Yojson.Safe.t =
    `Assoc
      ([ "backend", `String "ocaml-native"
       ; "schema_version", `Int 1
       ; ( "label"
         , `String (if !label = "" then measured.label ^ "-evaluation" else !label) )
       ; "policy", `Assoc [ "id", `String measured.label; "kind", `String measured.kind ]
       ; "candidate", `String (if !candidate = "" then "baseline:" ^ !baseline else !candidate)
       ; "opponents", `Int (Array.length opponents)
       ; ( "opponent_labels"
         , `List (Array.to_list (Array.map (fun e -> `String e.Evaluation.label) opponents)) )
       ; "seeds", `Int (List.length seeds)
       ; "seeds_file", `String !seeds_path
       ; "workload_copies", `Int !copies
       ; "threads", `Int (max 1 (min !threads (Array.length jobs)))
       ; "requested_threads", `Int !threads
         (* The flat block below is the Phase 5 benchmark's correctness contract
            (tools/benchmark_policy.py CORRECTNESS_FIELDS and METRIC_FIELDS); it must
            keep its names and meanings. Everything richer is additive. *)
       ; "games", `Int overall.games
       ; "turns", `Int overall.transitions
       ; "wins", `Int overall.wins
       ; "draws", `Int overall.draws
       ; "losses", `Int overall.losses
       ; "candidate_money_total", `Float candidate_total
       ; "opponent_money_total", `Float opponent_total
       ; "mean_candidate_money", `Float (candidate_total /. games_float)
       ; "mean_opponent_money", `Float (opponent_total /. games_float)
       ; "mean_margin", `Float ((candidate_total -. opponent_total) /. games_float)
       ; "games_per_second", `Float (games_float /. seconds)
       ; "nanoseconds_per_turn"
       , `Float (seconds *. 1.0e9 /. float_of_int overall.transitions)
       ; "overall", Evaluation.json_of_summary overall
       ; ( "by_opponent"
         , `Assoc
             (List.map
                (fun (name, summary) -> name, Evaluation.json_of_summary summary)
                per_opponent) )
       ; ( "by_position"
         , `Assoc
             (List.map
                (fun (name, summary) -> name, Evaluation.json_of_summary summary)
                by_seat) )
       ; ( "worst_matchup"
         , match worst with
           | None -> `Null
           | Some (name, rate) ->
             `Assoc [ "opponent", `String name; "score_rate", `Float rate ] )
       ]
       @ (match timing_json with
          | `Assoc fields -> fields
          | _ -> [])
       @ if !want_coverage then [ "coverage", coverage_json ] else [])
  in
  if !artifact <> "" then write_artifact !artifact summary_json;
  print_endline (Yojson.Safe.to_string summary_json);
  if !want_coverage && missing <> []
  then (
    Printf.eprintf
      "coverage gate failed: %s never emitted %s\n"
      measured.label
      (String.concat ", " missing);
    exit 1)
;;

(* ---------------- league ---------------- *)

(* Round-robin over an entrant population: every unordered pair, both seat orders, every
   seed. Self-play is excluded — an entrant against itself is a tautology at these
   settings, since both sides are deterministic and identically seeded. *)
let league argv =
  let entrants_path = ref ""
  and seeds_path = ref ""
  and family = ref ""
  and threads = ref 1
  and artifact = ref ""
  and label = ref "baseline-league"
  and want_coverage = ref true in
  parse_options
    argv
    ~options:
      [ "--entrants", (fun value -> entrants_path := value)
      ; "--seeds", (fun value -> seeds_path := value)
      ; "--family", (fun value -> family := value)
      ; "--artifact", (fun value -> artifact := value)
      ; "--label", (fun value -> label := value)
      ; "--threads", positive_int "--threads" (fun value -> threads := value)
      ; "--no-coverage!flag", (fun _ -> want_coverage := false)
      ];
  if !entrants_path = "" then failwith "league requires --entrants";
  if !seeds_path = "" then failwith "league requires --seeds";
  let ambient_family = if !family = "" then None else Some !family in
  let entrants = Array.of_list (Evaluation.of_file ~ambient_family !entrants_path) in
  if Array.length entrants < 2 then failwith "a league needs at least two entrants";
  let seeds = read_seeds !seeds_path in
  let count = Array.length entrants in
  let jobs =
    Array.of_list
      (List.concat_map
         (fun first ->
           List.concat_map
             (fun second ->
               if second <= first
               then []
               else
                 List.concat_map
                   (fun seed ->
                     [ { seat_a = first; seat_b = second; seed }
                     ; { seat_a = second; seat_b = first; seed }
                     ])
                   seeds)
             (List.init count Fun.id))
         (List.init count Fun.id))
  in
  let outcomes = Array.make (Array.length jobs) None in
  let coverage =
    if !want_coverage then Some (Array.make (Array.length jobs) None) else None
  in
  let cpu_started = Unix.times () in
  let started = Unix.gettimeofday () in
  run_jobs ~threads:!threads ~entrants ~jobs ~outcomes ~coverage;
  let config = Model.default_config in
  let failures = ref [] in
  let rows =
    List.init count (fun entrant ->
      let games = games_for ~jobs ~outcomes ~entrant ~opponent_of:Fun.id in
      let overall = Evaluation.summarize ~config games in
      let per_opponent =
        List.filter_map
          (fun opponent ->
            if opponent = entrant
            then None
            else (
              let subset =
                List.filter (fun (g : Evaluation.game) -> g.opponent = opponent) games
              in
              Some
                ( entrants.(opponent).Evaluation.label
                , Evaluation.summarize ~config subset )))
          (List.init count Fun.id)
      in
      let by_seat =
        List.map
          (fun seat ->
            let subset = List.filter (fun (g : Evaluation.game) -> g.seat = seat) games in
            ( (if seat = 0 then "player_0" else "player_1")
            , Evaluation.summarize ~config subset ))
          [ 0; 1 ]
      in
      let worst =
        List.fold_left
          (fun worst (name, (summary : Evaluation.summary)) ->
            match worst with
            | Some (_, best) when best <= summary.score_rate -> worst
            | _ -> Some (name, summary.score_rate))
          None
          per_opponent
      in
      let coverage_json =
        if not !want_coverage
        then []
        else (
          let total = merge_coverage ~jobs ~coverage ~entrant in
          let missing, json = coverage_report ~entrant:entrants.(entrant) total in
          if missing <> []
          then failures := (entrants.(entrant).Evaluation.label, missing) :: !failures;
          [ "coverage", json ])
      in
      ( entrants.(entrant).Evaluation.label
      , overall
      , `Assoc
          ([ "id", `String entrants.(entrant).Evaluation.label
           ; "kind", `String entrants.(entrant).Evaluation.kind
           ; "overall", Evaluation.json_of_summary overall
           ; ( "by_opponent"
             , `Assoc
                 (List.map
                    (fun (name, summary) -> name, Evaluation.json_of_summary summary)
                    per_opponent) )
           ; ( "by_position"
             , `Assoc
                 (List.map
                    (fun (name, summary) -> name, Evaluation.json_of_summary summary)
                    by_seat) )
           ; ( "worst_matchup"
             , match worst with
               | None -> `Null
               | Some (name, rate) ->
                 `Assoc [ "opponent", `String name; "score_rate", `Float rate ] )
           ]
           @ coverage_json) ))
  in
  let total_transitions =
    List.fold_left
      (fun acc (_, (summary : Evaluation.summary), _) -> acc + summary.transitions)
      0
      rows
  in
  (* Each game is counted by both its participants, so the league's own turn total is
     half the sum of the rows'. *)
  let _, _, timing_json = timing ~started ~cpu_started ~transitions:(total_transitions / 2) in
  let ranked =
    List.sort
      (fun (_, (a : Evaluation.summary), _) (_, (b : Evaluation.summary), _) ->
        compare b.score_rate a.score_rate)
      rows
  in
  let document : Yojson.Safe.t =
    `Assoc
      ([ "backend", `String "ocaml-native"
       ; "schema_version", `Int 1
       ; "label", `String !label
       ; "entrants_file", `String !entrants_path
       ; "seeds_file", `String !seeds_path
       ; "seeds", `Int (List.length seeds)
       ; "games", `Int (Array.length jobs)
       ; "turns", `Int (total_transitions / 2)
       ; "threads", `Int (max 1 (min !threads (Array.length jobs)))
       ; ( "table"
         , `List (List.map (fun (_, _, row) -> row) ranked) )
       ]
       @
       match timing_json with
       | `Assoc fields -> fields
       | _ -> [])
  in
  if !artifact <> "" then write_artifact !artifact document;
  print_endline (Yojson.Safe.to_string document);
  (* A human-readable table on stderr: the artifact is the record, this is the read. *)
  Printf.eprintf
    "\n%-16s %7s %7s %6s %6s %6s %11s %11s %8s  %s\n"
    "entrant"
    "score"
    "win"
    "W"
    "D"
    "L"
    "margin_mean"
    "margin_med"
    "catloss"
    "worst matchup";
  List.iter
    (fun (name, (summary : Evaluation.summary), row) ->
      let worst =
        match Evaluation.json_field "worst_matchup" row with
        | Some (`Assoc fields) ->
          (match List.assoc_opt "opponent" fields, List.assoc_opt "score_rate" fields with
           | Some (`String opponent), Some (`Float rate) ->
             Printf.sprintf "%s (%.3f)" opponent rate
           | _ -> "-")
        | _ -> "-"
      in
      Printf.eprintf
        "%-16s %7.3f %7.3f %6d %6d %6d %11.1f %11.1f %7.1f%%  %s\n"
        name
        summary.score_rate
        (float_of_int summary.wins /. float_of_int (max 1 summary.games))
        summary.wins
        summary.draws
        summary.losses
        (Evaluation.mean summary.margins)
        (Evaluation.median summary.margins)
        (100.0 *. float_of_int summary.catastrophic /. float_of_int (max 1 summary.games))
        worst)
    ranked;
  if !failures <> []
  then (
    List.iter
      (fun (name, missing) ->
        Printf.eprintf
          "coverage gate failed: %s never emitted %s\n"
          name
          (String.concat ", " missing))
      !failures;
    exit 1)
;;
