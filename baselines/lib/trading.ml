(* Market-queue construction shared by the baselines, plus the endgame rule they all obey.

   The endgame rule is not a refinement, it is a correctness requirement. The episode's
   last transition is day 29 hour 23, and end-of-day never fires for it, so whatever a
   unit is still carrying at the end is simply lost — and the shed itself is worth
   nothing either, since reward is the bank balance. Every baseline therefore spends its
   last turns walking to the shed, dropping, and selling. *)

module Model = Kag_model.Model

let product_count = Model.product_count

(* Buy seeds up to [target], never spending below [reserve]. One order carries the whole
   count: the market lockstep commits it unit by unit and stops when money runs out, so
   an over-large count is throttled rather than refused. *)
let restock_seeds obs ~crop ~target ~reserve =
  let want = target - Farm_view.seeds obs ~crop in
  if want <= 0
  then None
  else (
    let cost = Model.seed_costs.(crop) in
    let affordable = (Farm_view.money obs - reserve) / cost in
    let count = min want (max 0 affordable) in
    if count > 0 then Some (Model.Buy_seed { crop; count }) else None)
;;

(* Sell the whole shed holding of [item] once it is worth a trip: at least [min_units] on
   hand and a price at or above [min_price]. *)
let sell_holding obs ~item ~min_units ~min_price =
  let have = Farm_view.shed obs ~item in
  if have >= min_units && Farm_view.price obs ~item >= min_price
  then Some (Model.Sell { item; count = have })
  else None
;;

(* Two endgame thresholds, not one, because they answer different questions.

   [selling_out] is when the market queue stops holding anything back and dumps the
   shed; it can start early, since a sale is a sale. [dumping] is when units must be
   walking their carried goods to the shed, and it must start late enough that they are
   still harvesting and early enough that a dropped unit's goods get one more turn to be
   sold. The last end-of-day fires at transition 695 (day 28 hour 23); every harvest
   after it stays in a unit's inventory unless someone drops it, and the reward is the
   bank balance, so anything still carried at transition 719 is simply thrown away. *)
let within_final_turns obs ~(config : Model.config) ~turns =
  Farm_view.turns_remaining obs ~episode_steps:config.episode_steps <= turns
;;

(* Sell everything sellable, most valuable first, within the per-turn order cap. Animals
   held in the shed (indices >= product_count) have no market and are skipped. *)
let liquidation_orders obs ~(config : Model.config) =
  let holdings =
    List.filter
      (fun item -> Farm_view.shed obs ~item > 0)
      (List.init product_count Fun.id)
  in
  let by_value =
    List.sort
      (fun a b -> compare (Farm_view.price obs ~item:b) (Farm_view.price obs ~item:a))
      holdings
  in
  let rec take n = function
    | [] -> []
    | item :: rest ->
      if n = 0
      then []
      else Model.Sell { item; count = Farm_view.shed obs ~item } :: take (n - 1) rest
  in
  Array.of_list (take config.max_market_orders_per_turn by_value)
;;

let nearest_shed_access obs ~position =
  let access = Model.shed_access_tiles ~board_size:(Farm_view.board_size obs) in
  let best = ref access.(0) in
  Array.iter
    (fun tile ->
      if Farm_view.manhattan position tile < Farm_view.manhattan position !best
      then best := tile)
    access;
  !best
;;

(* During the dump phase a unit carrying goods walks to the shed and drops; a unit
   carrying nothing keeps doing its normal job, so the last day still harvests rather
   than standing idle waiting to carry something. *)
let dump_ops obs ~fallback =
  let op_for_unit unit =
    let position = Farm_view.unit_pos obs ~unit in
    if Farm_view.carried_total obs ~unit = 0
    then fallback ~unit
    else if Farm_view.on_shed_access obs ~unit
    then Model.Drop
    else (
      match
        Farm_view.step_toward ~from:position ~target:(nearest_shed_access obs ~position)
      with
      | Some move -> move
      | None -> Model.Drop)
  in
  let units = Farm_view.unit_count obs in
  op_for_unit 0, Array.init (units - 1) (fun index -> op_for_unit (index + 1))
;;
