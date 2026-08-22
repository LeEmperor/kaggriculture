(* Livestock production: build structures, stock them, feed and care daily, sell the
   animal products.

   The interesting property of this baseline is that it is not self-contained. Animals eat
   WHEAT, one unit per animal per night, and two consecutive unfed nights lose the animal
   back to an empty structure — so a livestock strategy is really a livestock strategy
   plus a feed supply. This one runs both: the farmer keeps the animals and hired hands
   run a wheat plot, with market-bought wheat as the fallback when the plot is short.
   Whether growing feed beats buying it is exactly the kind of question the league table
   exists to answer, so both paths are present rather than one being assumed. *)

module Model = Kag_model.Model

type spec =
  { animal : int (* index into Model.animal_* *)
  ; animal_target : int
  ; feed_plot_tiles : int
  ; hands_target : int
  ; reserve : int
  ; feed_stock : int (* shed WHEAT kept as a buffer *)
  ; feed_price_cap : int (* never buy feed above this price *)
  ; sell_batch : int
  ; sell_out_turns : int
  ; dump_turns : int
  }

let wheat = 0
let structure_kind spec = Model.animal_structure.(spec.animal)
let boxed_item spec = Model.shed_index_of_animal spec.animal
let product spec = Model.animal_product.(spec.animal)

let animals_owned obs =
  Farm_view.count_owned obs ~pred:(fun ~x:_ ~y:_ tile ->
    match tile with
    | Model.Animal _ -> true
    | _ -> false)
;;

let structures_owned obs spec =
  Farm_view.count_owned obs ~pred:(fun ~x:_ ~y:_ tile ->
    match tile with
    | Model.Structure kind -> kind = structure_kind spec
    | _ -> false)
;;

(* Animals that will go hungry tonight unless someone reaches them with wheat. *)
let unfed obs =
  Farm_view.count_owned obs ~pred:(fun ~x:_ ~y:_ tile ->
    match tile with
    | Model.Animal animal -> not animal.fed_today
    | _ -> false)
;;

(* The farmer's job list, ranked. Harvest first because an animal at its held-units cap
   stops producing; feeding next because missing two nights loses the animal outright. *)
let animal_task spec ~carrying_wheat ~carrying_animal ~may_build tile =
  match tile with
  | Model.Animal animal ->
    if animal.yield_units > 0
    then Some (0, Model.Harvest)
    else if (not animal.fed_today) && carrying_wheat > 0
    then Some (1, Model.Feed)
    else if animal.fertilizer_available
    then Some (3, Model.Collect_fertilizer)
    else if not animal.cared_today
    then Some (4, Model.Care)
    else None
  | Model.Structure kind when kind = structure_kind spec && carrying_animal > 0 ->
    Some (2, Model.Place { item = boxed_item spec; count = 1 })
  | Model.Empty when may_build ->
    Some (5, if structure_kind spec = 0 then Model.Build_coop else Model.Build_pasture)
  | _ -> None
;;

let farmer_op obs spec =
  let carrying_wheat = Farm_view.carrying obs ~unit:0 ~item:wheat in
  let carrying_animal = Farm_view.carrying obs ~unit:0 ~item:(boxed_item spec) in
  let shed_wheat = Farm_view.shed obs ~item:wheat in
  let shed_animal = Farm_view.shed obs ~item:(boxed_item spec) in
  let wheat_short = max 0 (unfed obs - carrying_wheat) in
  (* Hoisted out of the per-tile score: both are whole-farm scans, and calling them
     inside [best_owned]'s callback would make each turn quadratic in the board. *)
  let empty_structures = structures_owned obs spec in
  let may_build = empty_structures + animals_owned obs < spec.animal_target in
  let wants_animal = empty_structures > 0 && shed_animal > 0 in
  let position = Farm_view.unit_pos obs ~unit:0 in
  if Farm_view.on_shed_access obs ~unit:0 && wheat_short > 0 && shed_wheat > 0
  then Model.Pickup { item = wheat; count = min wheat_short shed_wheat }
  else if Farm_view.on_shed_access obs ~unit:0 && wants_animal && carrying_animal = 0
  then Model.Pickup { item = boxed_item spec; count = 1 }
  else (
    let target =
      Farm_view.best_owned obs ~from:position ~score:(fun ~x:_ ~y:_ tile ->
        match animal_task spec ~carrying_wheat ~carrying_animal ~may_build tile with
        | None -> None
        | Some (rank, _) -> Some rank)
    in
    let needs_shed = (wheat_short > 0 && shed_wheat > 0) || (wants_animal && carrying_animal = 0) in
    let urgent =
      match target with
      | None -> false
      | Some (x, y) ->
        (match
           animal_task
             spec
             ~carrying_wheat
             ~carrying_animal
             ~may_build
             (Farm_view.tile obs ~x ~y)
         with
         | Some (rank, _) -> rank <= 1
         | None -> false)
    in
    (* A trip back for feed outranks the low-priority chores but not a harvest or a
       feeding that can happen right now. *)
    if needs_shed && not urgent
    then (
      let access = Model.shed_access_tiles ~board_size:(Farm_view.board_size obs) in
      let best = ref access.(0) in
      Array.iter
        (fun tile ->
          if Farm_view.manhattan position tile < Farm_view.manhattan position !best
          then best := tile)
        access;
      match Farm_view.step_toward ~from:position ~target:!best with
      | Some move -> move
      | None -> Model.Unit_pass)
    else (
      match target with
      | None -> Model.Unit_pass
      | Some (tx, ty) ->
        if position = (tx, ty)
        then (
          match
            animal_task
              spec
              ~carrying_wheat
              ~carrying_animal
              ~may_build
              (Farm_view.tile obs ~x:tx ~y:ty)
          with
          | Some (_, op) -> op
          | None -> Model.Unit_pass)
        else (
          match Farm_view.step_toward ~from:position ~target:(tx, ty) with
          | Some move -> move
          | None -> Model.Unit_pass)))
;;

let market obs spec ~(config : Model.config) =
  let max_orders = config.max_market_orders_per_turn in
  if Trading.within_final_turns obs ~config ~turns:spec.sell_out_turns
  then Trading.liquidation_orders obs ~config
  else (
    let have_hands = Array.length (Farm_view.me obs).hands in
    let hires = List.init (max 0 (spec.hands_target - have_hands)) (fun _ -> Model.Hire) in
    let money = Farm_view.money obs in
    let buy_animal =
      let owned = animals_owned obs + Farm_view.shed obs ~item:(boxed_item spec) in
      if owned < spec.animal_target
         && money >= Model.animal_costs.(spec.animal) + spec.reserve
         && Farm_view.shed_room obs ~capacity:config.shed_capacity > 0
      then Some (Model.Buy_animal { animal = spec.animal; count = 1 })
      else None
    in
    (* Feed is bought only as a top-up: the plot is meant to carry it, and buying wheat
       at market price to make animal product is the losing half of this baseline. *)
    let buy_feed =
      let short = spec.feed_stock - Farm_view.shed obs ~item:wheat in
      if short > 0
         && Farm_view.price obs ~item:wheat <= spec.feed_price_cap
         && money >= spec.reserve + (short * Farm_view.price obs ~item:wheat)
      then Some (Model.Buy_product { item = wheat; count = short })
      else None
    in
    let orders =
      hires
      @ List.filter_map
          Fun.id
          [ buy_animal
          ; buy_feed
          ; Trading.restock_seeds
              obs
              ~crop:wheat
              ~target:spec.feed_plot_tiles
              ~reserve:spec.reserve
          ; Trading.sell_holding
              obs
              ~item:(product spec)
              ~min_units:spec.sell_batch
              ~min_price:1
          ; Trading.sell_holding
              obs
              ~item:Model.fertilizer_item
              ~min_units:spec.sell_batch
              ~min_price:1
          ]
    in
    Array.of_list (List.filteri (fun index _ -> index < max_orders) orders))
;;

let create spec (config : Model.config) ~seat:_ : Model.policy =
  fun obs ->
  let hand_count = Farm_view.unit_count obs - 1 in
  let assigned =
    Tending.assign_units
      obs
      ~plan:
        (Tending.plan_for
           obs
           ~config
           ~crop:wheat
           ~max_tiles:spec.feed_plot_tiles
           ~harvest_age:4)
      ~units:(List.init hand_count (fun index -> index + 1))
  in
  let normal ~unit =
    if unit = 0
    then farmer_op obs spec
    else (
      match List.assoc_opt unit assigned with
      | Some op -> op
      | None -> Model.Unit_pass)
  in
  let farmer, hands =
    if Trading.within_final_turns obs ~config ~turns:spec.dump_turns
    then Trading.dump_ops obs ~fallback:normal
    else
      ( normal ~unit:0
      , Array.init hand_count (fun index -> normal ~unit:(index + 1)) )
  in
  { Model.farmer; hands; market = market obs spec ~config }
;;
