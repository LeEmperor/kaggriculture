(* The Phase 6 evaluation layer: who is playing, what is measured, and how it is written
   down. Consumed by [kag_sim evaluate] and [kag_sim league].

   Three things live here because both commands need all three:

   - [entrant], which resolves a spec — a baseline id, or a family/candidate pair — into
     something that can be constructed per game. An evaluation population is deliberately
     heterogeneous: the DSL's game seam is a one-tile, one-worker slice, so the baselines
     are native policies, and a league in which only one representation could enter would
     not be a league.
   - the statistic vocabulary of the game plan's Phase 7 "Reported statistics" list,
     computed once from per-game records rather than from running totals, because
     medians, percentiles, and intervals cannot be accumulated.
   - the JSON artifact, which is what a promotion decision is later checked against. *)

module Model = Kag_model.Model

(* ---------------- loading ---------------- *)

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

let load_family path =
  Policy_dsl.Family.load
    (read_json path)
    ~observations:Kag_vocabulary.Native_vocabulary.t.Policy_dsl.Interpreter.kinds
    ~emits:Kag_vocabulary.Native_actions.emits
;;

let candidate_parameters (family : Policy_dsl.Family.t) path =
  let candidate = read_json path in
  (match json_field "policy_id" candidate with
   | Some (`String id) when id = family.policy_id -> ()
   | Some (`String id) ->
     failwith
       (Printf.sprintf "%s targets policy_id %S but the family is %S" path id family.policy_id)
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

(* ---------------- entrants ---------------- *)

type entrant =
  { label : string
  ; kind : string (* "baseline" or "dsl" *)
  ; expect_ops : Kag_baselines.Coverage.op_tag list
  ; expect_orders : Kag_baselines.Coverage.order_tag list
  ; create : Model.config -> seat:int -> Model.policy
  }

let of_baseline (baseline : Kag_baselines.Registry.t) =
  { label = baseline.id
  ; kind = "baseline"
  ; expect_ops = baseline.expect_ops
  ; expect_orders = baseline.expect_orders
  ; create = baseline.create
  }
;;

let baseline_entrant id =
  match Kag_baselines.Registry.find id with
  | Some baseline -> of_baseline baseline
  | None ->
    failwith
      (Printf.sprintf
         "unknown baseline %S; known baselines are %s"
         id
         (String.concat ", " Kag_baselines.Registry.ids))
;;

(* A DSL entrant's interpreter is built once and shared by every game; only the register
   bank is per game, which is what [Interpreter.Policy.create] allocates. *)
let dsl_entrant ~family_path ~candidate_path =
  let family = load_family family_path in
  let interpreter =
    Policy_dsl.Interpreter.create
      ~family
      ~parameters:(candidate_parameters family candidate_path)
      ~vocabulary:Kag_vocabulary.Native_vocabulary.t
      ~build_action:Kag_vocabulary.Native_actions.build_action
  in
  { label = Filename.basename candidate_path
  ; kind = "dsl"
  ; expect_ops = []
  ; expect_orders = []
  ; create =
      (fun _config ~seat:_ ->
        Policy_dsl.Interpreter.Policy.act (Policy_dsl.Interpreter.Policy.create interpreter))
  }
;;

let resolve ~base_dir path =
  if Filename.is_relative path then Filename.concat base_dir path else path
;;

(* Spec forms, in the order a reader will meet them:

     "pass"                                  the PASS baseline (kept for the Phase 5
                                             benchmark manifest, which predates the rest)
     "baseline:crop-greedy"                  a registry baseline
     "candidate.json"                        a candidate under the ambient --family
     {"baseline": "crop-greedy"}
     {"family": "f.json", "candidate": "c.json"}

   The object form is what makes a heterogeneous population expressible: each entrant
   carries its own family, so a league is not restricted to one encoding. *)
let of_spec ~base_dir ~ambient_family json =
  match json with
  | `String "pass" -> baseline_entrant "pass"
  | `String spec when String.length spec > 9 && String.sub spec 0 9 = "baseline:" ->
    baseline_entrant (String.sub spec 9 (String.length spec - 9))
  | `String candidate ->
    (match ambient_family with
     | Some family_path ->
       dsl_entrant ~family_path ~candidate_path:(resolve ~base_dir candidate)
     | None ->
       failwith
         (Printf.sprintf
            "%S names a candidate file but no --family was given; use \
             {\"family\": ..., \"candidate\": ...} or baseline:<id>"
            candidate))
  | `Assoc _ as document ->
    (match json_field "baseline" document, json_field "candidate" document with
     | Some (`String id), None -> baseline_entrant id
     | None, Some (`String candidate) ->
       let family_path =
         match json_field "family" document, ambient_family with
         | Some (`String family), _ -> resolve ~base_dir family
         | None, Some family -> family
         | None, None ->
           failwith "an entrant object with a candidate needs a family, here or as --family"
         | Some other, _ ->
           failwith ("family must be a string, got " ^ Yojson.Safe.to_string other)
       in
       dsl_entrant ~family_path ~candidate_path:(resolve ~base_dir candidate)
     | _ ->
       failwith
         ("an entrant object must have exactly one of \"baseline\" or \"candidate\": "
          ^ Yojson.Safe.to_string document))
  | other -> failwith ("cannot read entrant spec " ^ Yojson.Safe.to_string other)
;;

let of_file ~ambient_family path =
  let base_dir = Filename.dirname path in
  match read_json path with
  | `List [] -> failwith (path ^ " lists no entrants")
  | `List specs -> List.map (of_spec ~base_dir ~ambient_family) specs
  | _ -> failwith (path ^ " must be a JSON array")
;;

(* ---------------- per-game records ---------------- *)

type game =
  { opponent : int (* index into the opponent list *)
  ; seat : int (* the seat the measured policy occupied: 0 or 1 *)
  ; seed : int
  ; money : float (* measured policy's final money *)
  ; opponent_money : float
  ; transitions : int
  }

let margin game = game.money -. game.opponent_money

(* ---------------- statistics ---------------- *)

let mean values =
  if Array.length values = 0
  then 0.0
  else Array.fold_left ( +. ) 0.0 values /. float_of_int (Array.length values)
;;

(* Sample variance (n-1). Zero for a single game rather than undefined: the interval
   built from it is then degenerate, which is the honest answer for one observation. *)
let variance values =
  let n = Array.length values in
  if n < 2
  then 0.0
  else (
    let m = mean values in
    Array.fold_left (fun acc v -> acc +. ((v -. m) ** 2.0)) 0.0 values
    /. float_of_int (n - 1))
;;

(* Nearest-rank percentile on an already sorted array: no interpolation, so every
   reported percentile is a value that actually occurred in some game. *)
let percentile sorted p =
  let n = Array.length sorted in
  if n = 0
  then 0.0
  else (
    let rank = int_of_float (ceil (p /. 100.0 *. float_of_int n)) in
    sorted.(max 0 (min (n - 1) (rank - 1))))
;;

let median sorted =
  let n = Array.length sorted in
  if n = 0
  then 0.0
  else if n mod 2 = 1
  then sorted.(n / 2)
  else (sorted.((n / 2) - 1) +. sorted.(n / 2)) /. 2.0
;;

(* Wilson score interval. The normal approximation is useless exactly where a promotion
   decision is most delicate — a win rate near 0 or 1 — and this one stays inside [0, 1]
   there. *)
let wilson ~successes ~trials =
  if trials = 0
  then 0.0, 0.0
  else (
    let z = 1.959963984540054 in
    let n = float_of_int trials in
    let p = float_of_int successes /. n in
    let denominator = 1.0 +. (z *. z /. n) in
    let centre = (p +. (z *. z /. (2.0 *. n))) /. denominator in
    let spread =
      z
      /. denominator
      *. sqrt ((p *. (1.0 -. p) /. n) +. (z *. z /. (4.0 *. n *. n)))
    in
    max 0.0 (centre -. spread), min 1.0 (centre +. spread))
;;

type summary =
  { games : int
  ; wins : int
  ; draws : int
  ; losses : int
  ; score_rate : float (* wins + half the draws, over games *)
  ; margins : float array (* sorted *)
  ; money_mean : float
  ; opponent_money_mean : float
  ; catastrophic : int (* games finishing below the starting bankroll *)
  ; transitions : int
  }

let summarize ~(config : Model.config) games =
  let raw = Array.of_list games in
  let margins = Array.map margin raw in
  let sorted = Array.copy margins in
  Array.sort compare sorted;
  let wins = ref 0
  and draws = ref 0
  and losses = ref 0
  and catastrophic = ref 0
  and transitions = ref 0 in
  Array.iter
    (fun game ->
      let m = margin game in
      if m > 0.0 then incr wins else if m < 0.0 then incr losses else incr draws;
      if game.money < float_of_int config.starting_money then incr catastrophic;
      transitions := !transitions + game.transitions)
    raw;
  let n = Array.length raw in
  { games = n
  ; wins = !wins
  ; draws = !draws
  ; losses = !losses
  ; score_rate =
      (if n = 0
       then 0.0
       else (float_of_int !wins +. (0.5 *. float_of_int !draws)) /. float_of_int n)
  ; margins = sorted
  ; money_mean = mean (Array.map (fun game -> game.money) raw)
  ; opponent_money_mean = mean (Array.map (fun game -> game.opponent_money) raw)
  ; catastrophic = !catastrophic
  ; transitions = !transitions
  }
;;

let json_of_summary (s : summary) : Yojson.Safe.t =
  let n = float_of_int (max 1 s.games) in
  let m = mean s.margins in
  let var = variance s.margins in
  let stderr = if s.games < 2 then 0.0 else sqrt (var /. float_of_int s.games) in
  let win_low, win_high = wilson ~successes:s.wins ~trials:s.games in
  `Assoc
    [ "games", `Int s.games
    ; "turns", `Int s.transitions
    ; "wins", `Int s.wins
    ; "draws", `Int s.draws
    ; "losses", `Int s.losses
    ; "win_rate", `Float (float_of_int s.wins /. n)
    ; "score_rate", `Float s.score_rate
    ; "win_rate_ci95", `List [ `Float win_low; `Float win_high ]
    ; "margin_mean", `Float m
    ; "margin_median", `Float (median s.margins)
    ; "margin_variance", `Float var
    ; "margin_stddev", `Float (sqrt var)
    ; "margin_stderr", `Float stderr
    ; ( "margin_mean_ci95"
      , `List [ `Float (m -. (1.959963984540054 *. stderr))
              ; `Float (m +. (1.959963984540054 *. stderr))
              ] )
    ; "margin_p5", `Float (percentile s.margins 5.0)
    ; "margin_p95", `Float (percentile s.margins 95.0)
    ; "margin_min", `Float (if s.games = 0 then 0.0 else s.margins.(0))
    ; "margin_max", `Float (if s.games = 0 then 0.0 else s.margins.(s.games - 1))
    ; "money_mean", `Float s.money_mean
    ; "opponent_money_mean", `Float s.opponent_money_mean
    ; "catastrophic_losses", `Int s.catastrophic
    ; "catastrophic_loss_rate", `Float (float_of_int s.catastrophic /. n)
    ]
;;

let json_of_coverage (coverage : Kag_baselines.Coverage.t) : Yojson.Safe.t =
  let counts names count =
    `Assoc
      (List.filter_map
         (fun (tag, name) ->
           let value = count tag in
           if value = 0 then None else Some (name, `Int value))
         names)
  in
  `Assoc
    [ "turns", `Int coverage.turns
    ; "hand_actions", `Int coverage.hand_actions
    ; ( "unit_ops"
      , counts Kag_baselines.Coverage.op_tags (Kag_baselines.Coverage.op_count coverage) )
    ; ( "market_orders"
      , counts
          Kag_baselines.Coverage.order_tags
          (Kag_baselines.Coverage.order_count coverage) )
    ]
;;
