(* Uniformly random play over the *applicable* action menu.

   "Random valid actions" (game plan, Phase 6) is read here as applicable rather than
   merely well-formed. Upstream silently no-ops an inapplicable action, so a policy
   drawing from the full grammar would spend almost every turn doing nothing and would be
   a floor on the scoreboard without being a floor on behaviour. Rebuilding the engine's
   own guards per tile costs a little duplication and buys a control that actually
   touches the rule set — which is what makes it useful as the population's lower bound
   and as a fuzz source for the evaluation harness.

   The draws come from [Rng], seeded from the game seed and the seat, so a run replays
   exactly. *)

module Model = Kag_model.Model

type spec =
  { market_numerator : int (* chance of emitting a market queue this turn *)
  ; market_denominator : int
  ; max_orders : int
  }

let tradeable_products = [| 0; 1; 2; 3; 4; 5; 6; 7; 8 |]
let buyable_products = [| 0; Model.fertilizer_item |] (* upstream's BUY_PRODUCT domain *)

(* The ops that would actually change something, for the unit standing on [tile]. Moves
   are included whenever they stay on the board — walking is always applicable, and
   without it the walker never leaves its spawn. *)
let menu obs ~unit ~sow_budget =
  let x, y = Farm_view.unit_pos obs ~unit in
  let size = Farm_view.board_size obs in
  let inventory = Farm_view.unit_inventory obs ~unit in
  let carrying item = inventory.Model.counts.(item) in
  let ops = ref [ Model.Unit_pass ] in
  let add op = ops := op :: !ops in
  if y > 0 then add (Model.Move Model.North);
  if y < size - 1 then add (Model.Move Model.South);
  if x > 0 then add (Model.Move Model.West);
  if x < size - 1 then add (Model.Move Model.East);
  if Farm_view.on_shed_access obs ~unit
  then (
    if Farm_view.carried_total obs ~unit > 0 then add Model.Drop;
    Array.iteri
      (fun item count -> if count > 0 then add (Model.Pickup { item; count = 1 }))
      (Farm_view.priv obs).shed;
    Array.iteri
      (fun item count -> if count > 0 then add (Model.Place { item; count = 1 }))
      inventory.Model.counts);
  (match Farm_view.tile obs ~x ~y with
   | Model.Locked -> ()
   | Model.Empty ->
     add Model.Build_coop;
     add Model.Build_pasture;
     for crop = 0 to Model.crop_count - 1 do
       if Farm_view.seeds obs ~crop > 0 && sow_budget crop > 0
       then add (Model.Plant_crop { crop })
     done
   | Model.Weed -> add Model.Dig
   | Model.Structure kind ->
     add Model.Dig;
     for animal = 0 to Model.animal_count - 1 do
       let item = Model.shed_index_of_animal animal in
       if Model.animal_structure.(animal) = kind && carrying item > 0
       then add (Model.Place { item; count = 1 })
     done
   | Model.Plant plant ->
     add Model.Dig;
     if not plant.watered_today then add Model.Water;
     if plant.yield_units > 0
        && Farm_view.day obs - plant.planted_day >= Model.crop_first_yield_day.(plant.crop)
     then add Model.Harvest;
     if carrying Model.fertilizer_item > 0 then add Model.Fertilize
   | Model.Animal animal ->
     if animal.yield_units > 0 then add Model.Harvest;
     if (not animal.fed_today) && carrying 0 > 0 then add Model.Feed;
     if not animal.cared_today then add Model.Care;
     if animal.fertilizer_available then add Model.Collect_fertilizer);
  Array.of_list !ops
;;

let order_menu obs ~(config : Model.config) =
  let money = Farm_view.money obs in
  let room = Farm_view.shed_room obs ~capacity:config.shed_capacity > 0 in
  let orders = ref [] in
  let add order = orders := order :: !orders in
  for crop = 0 to Model.crop_count - 1 do
    if money >= Model.seed_costs.(crop) then add (Model.Buy_seed { crop; count = 1 })
  done;
  Array.iter
    (fun item -> if Farm_view.shed obs ~item > 0 then add (Model.Sell { item; count = 1 }))
    tradeable_products;
  Array.iter
    (fun item ->
      if room && money >= Farm_view.price obs ~item
      then add (Model.Buy_product { item; count = 1 }))
    buyable_products;
  for animal = 0 to Model.animal_count - 1 do
    if room && money >= Model.animal_costs.(animal)
    then add (Model.Buy_animal { animal; count = 1 })
  done;
  let farm = Farm_view.me obs in
  if money
     >= Model.hire_cost ~hires_today:farm.hires_today ~mult:config.farm_hand_cost_mult
  then add Model.Hire;
  let extra = farm.unlocked_quadrants - 1 in
  if extra < Array.length Model.land_prices && money >= Model.land_prices.(extra)
  then add Model.Buy_land;
  Array.of_list !orders
;;

let create spec (config : Model.config) ~seat : Model.policy =
  let rng = Rng.create ~label:"random-valid" ~seed:config.seed ~seat in
  fun obs ->
    (* Upstream voids *all* of a player's PLANT requests for a crop once they exceed the
       seeds on hand, so the draw has to respect the budget as it is spent, not just the
       seed count as it stood at the start of the turn. *)
    let planted = Array.make Model.crop_count 0 in
    let sow_budget crop = Farm_view.seeds obs ~crop - planted.(crop) in
    let draw unit =
      let op = Rng.pick rng (menu obs ~unit ~sow_budget) in
      (match op with
       | Model.Plant_crop { crop } -> planted.(crop) <- planted.(crop) + 1
       | _ -> ());
      op
    in
    let farmer = draw 0 in
    let hands = Array.init (Farm_view.unit_count obs - 1) (fun index -> draw (index + 1)) in
    let market =
      if Rng.chance
           rng
           ~numerator:spec.market_numerator
           ~denominator:spec.market_denominator
      then (
        let available = order_menu obs ~config in
        if Array.length available = 0
        then [||]
        else (
          let count = 1 + Rng.below rng (min spec.max_orders config.max_market_orders_per_turn) in
          Array.init count (fun _ -> Rng.pick rng available)))
      else [||]
    in
    { Model.farmer; hands; market }
;;
