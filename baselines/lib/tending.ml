(* The shared field-work loop: keep a set of tiles planted, watered, and harvested.

   Three of the six baselines are this loop with different crops and tile budgets, and
   the expansion baseline is this loop with hands attached, so it lives here once. It is
   deliberately myopic — one greedy assignment per turn, no lookahead — because these are
   the interpretable measuring sticks the game plan asks for before search, not
   candidates.

   Task ranks are the whole strategy:

     0 WATER    a plant unwatered for two consecutive nights becomes a weed, so this
                outranks everything; it is also what fills the yield window.
     1 HARVEST  can wait a turn, and for a non-ongoing crop waiting for the last
                watering is worth a unit.
     2 PLANT    only up to the tile budget and only from seeds already bought.
     3 DIG      reclaiming a weed is worth doing, last.

   Ties break on distance and then row-major index, so a tour is reproducible. *)

module Model = Kag_model.Model

type plan =
  { crop : int
  ; max_tiles : int (* simultaneously planted tiles, across all units *)
  ; harvest_age : int (* days since planting before a non-ongoing plant is taken *)
  ; sow : bool (* a seed planted now still has time to reach its first yield *)
  ; endgame : bool (* take whatever is ripe; watering can no longer pay for itself *)
  }

(* [sow] and [endgame] are what keep the last days of an episode from being spent on
   crops that cannot mature. Without them a wheat plot spends day 29 watering eight
   tiles it will never harvest and planting a seed on the final transition, and the
   reward — the bank balance — never sees any of it. *)
let days_remaining obs ~(config : Model.config) =
  Farm_view.turns_remaining obs ~episode_steps:config.episode_steps / config.turns_per_day
;;

let plan_for obs ~config ~crop ~max_tiles ~harvest_age =
  let left = days_remaining obs ~config in
  { crop
  ; max_tiles
  ; harvest_age
  ; sow = left > Model.crop_first_yield_day.(crop)
  ; endgame = left <= 1
  }
;;

let task_of obs ~plan ~sow_budget tile =
  match tile with
  | Model.Plant plant ->
    let age = Farm_view.day obs - plant.planted_day in
    let ripe =
      plant.yield_units > 0 && age >= Model.crop_first_yield_day.(plant.crop)
    in
    let ready =
      ripe
      && (Model.crop_ongoing.(plant.crop) || plan.endgame || age >= plan.harvest_age)
    in
    (* Watering normally outranks harvesting — a plant unwatered two nights running
       becomes a weed, and for a non-ongoing crop the last waterings are yield. In the
       endgame that reverses: an unharvested plant is worth nothing. *)
    let water_rank, harvest_rank = if plan.endgame then 1, 0 else 0, 1 in
    if plan.endgame && ready
    then Some (harvest_rank, Model.Harvest)
    else if not plant.watered_today
    then Some (water_rank, Model.Water)
    else if ready
    then Some (harvest_rank, Model.Harvest)
    else None
  | Model.Empty ->
    if plan.sow && sow_budget > 0
    then Some (2, Model.Plant_crop { crop = plan.crop })
    else None
  | Model.Weed -> Some (3, Model.Dig)
  | Model.Locked | Model.Structure _ | Model.Animal _ -> None
;;

let planted_count obs ~crop =
  Farm_view.count_owned obs ~pred:(fun ~x:_ ~y:_ tile ->
    match tile with
    | Model.Plant plant -> plant.crop = crop
    | _ -> false)
;;

(* One turn of work for every unit the player has.

   Units are assigned in order and claim their target tile, so two units never walk to
   the same job. The sowing budget is shared and decremented as PLANT ops are issued:
   upstream voids *all* of a player's PLANT requests for a crop when they exceed the
   seeds on hand, so overshooting by one would silently cancel the whole turn's planting
   rather than one tile's. *)
(* [units] is the list of unit indices to assign, in the order they get to claim work.
   Passing a subset is how the animal baseline keeps its farmer on livestock while its
   hands run a feed plot: the units left out of the list simply are not assigned here. *)
let assign_units obs ~plan ~units =
  let claimed = Hashtbl.create 16 in
  let sow_budget =
    ref
      (max
         0
         (min
            (Farm_view.seeds obs ~crop:plan.crop)
            (plan.max_tiles - planted_count obs ~crop:plan.crop)))
  in
  let op_for_unit unit =
    let position = Farm_view.unit_pos obs ~unit in
    let target =
      Farm_view.best_owned obs ~from:position ~score:(fun ~x ~y tile ->
        if Hashtbl.mem claimed (Farm_view.index_of obs ~x ~y)
        then None
        else (
          match task_of obs ~plan ~sow_budget:!sow_budget tile with
          | None -> None
          | Some (rank, _) -> Some rank))
    in
    match target with
    | None -> Model.Unit_pass
    | Some (tx, ty) ->
      Hashtbl.replace claimed (Farm_view.index_of obs ~x:tx ~y:ty) ();
      if position = (tx, ty)
      then (
        match task_of obs ~plan ~sow_budget:!sow_budget (Farm_view.tile obs ~x:tx ~y:ty) with
        | Some (_, (Model.Plant_crop _ as op)) ->
          decr sow_budget;
          op
        | Some (_, op) -> op
        | None -> Model.Unit_pass)
      else (
        match Farm_view.step_toward ~from:position ~target:(tx, ty) with
        | Some move -> move
        | None -> Model.Unit_pass)
  in
  List.map (fun unit -> unit, op_for_unit unit) units
;;

(* Every unit, farmer first. Returns the [farmer, hands] pair a [player_action] wants. *)
let assign obs ~plan =
  let count = Farm_view.unit_count obs in
  let assigned = assign_units obs ~plan ~units:(List.init count Fun.id) in
  let op unit = try List.assoc unit assigned with Not_found -> Model.Unit_pass in
  op 0, Array.init (count - 1) (fun index -> op (index + 1))
;;
