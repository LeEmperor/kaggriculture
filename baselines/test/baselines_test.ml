module Model = Kag_model.Model
module Coverage = Kag_baselines.Coverage
module Registry = Kag_baselines.Registry

(* Eight seeds in both seats. The gate is aggregate, not per game: some declared
   shapes are conditional on the episode (the endgame DROP fires only for a unit still
   carrying a harvest on the last day), so a single game proves nothing either way. *)
let seeds = [ 1000003; 20260822; 77; 4242; 999983; 31337; 8675309; 1 ]

(* One game, with the baseline in [seat] and PASS opposite, tallying only the baseline's
   own actions. Coverage is about what the policy emitted, not what the game contained. *)
let play (baseline : Registry.t) ~seed ~seat coverage =
  let config = { Model.default_config with seed } in
  let policy = baseline.create config ~seat in
  let pass _ = Model.pass_action in
  let on_actions ~turn:_ action_a action_b =
    Coverage.observe coverage (if seat = 0 then action_a else action_b)
  in
  let result =
    if seat = 0
    then Model.run_game ~on_actions config ~policy_a:policy ~policy_b:pass
    else Model.run_game ~on_actions config ~policy_a:pass ~policy_b:policy
  in
  result.final_money.(seat)
;;

let () =
  let failures = ref 0 in
  List.iter
    (fun (baseline : Registry.t) ->
      let coverage = Coverage.create () in
      let money = ref 0.0 in
      List.iter
        (fun seed ->
          List.iter
            (fun seat -> money := !money +. play baseline ~seed ~seat coverage)
            [ 0; 1 ])
        seeds;
      let games = List.length seeds * 2 in
      let missing =
        Coverage.missing
          coverage
          ~expect_ops:baseline.expect_ops
          ~expect_orders:baseline.expect_orders
      in
      let non_pass =
        coverage.Coverage.turns
        + coverage.Coverage.hand_actions
        - Coverage.op_count coverage Coverage.Pass
      in
      Printf.printf
        "%-16s mean_money=%9.1f  unit_actions=%6d non_pass=%6d  orders=%5d"
        baseline.id
        (!money /. float_of_int games)
        (coverage.Coverage.turns + coverage.Coverage.hand_actions)
        non_pass
        (Array.fold_left ( + ) 0 coverage.Coverage.orders);
      (match missing with
       | [] -> print_string "  coverage=ok\n"
       | missing ->
         incr failures;
         Printf.printf "  COVERAGE MISSING: %s\n" (String.concat " " missing));
      if Sys.getenv_opt "BASELINE_HISTOGRAM" <> None
      then (
        let cells =
          List.filter_map
            (fun (tag, name) ->
              let count = Coverage.op_count coverage tag in
              if count = 0 then None else Some (Printf.sprintf "%s=%d" name count))
            Coverage.op_tags
          @ List.filter_map
              (fun (tag, name) ->
                let count = Coverage.order_count coverage tag in
                if count = 0
                then None
                else
                  Some
                    (Printf.sprintf
                       "%s=%d(%du)"
                       name
                       count
                       (Coverage.order_unit_count coverage tag)))
              Coverage.order_tags
        in
        Printf.printf "    %s\n" (String.concat " " cells)))
    Registry.all;
  if !failures > 0
  then (
    Printf.eprintf "\n%d baseline(s) failed the coverage declaration\n" !failures;
    exit 1);
  print_endline "\nall baselines covered their declared action shapes"
;;
