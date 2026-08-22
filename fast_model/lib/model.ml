(* The scalar transition engine — the complete Phase 3 rule set (all seven rule groups of
   docs/kaggriculture_gameplan.md), ported rule-for-rule from the pinned upstream
   interpreter and held to it by the differential fixtures in ../test.

   Semantics that are deliberately exact rather than merely equivalent: inventory
   insertion order (Python dict order decides which items survive a capacity-limited
   drop), the market's two-phase per-unit lockstep, CPython's round-half-even in the
   pricing curves, and the end-of-day RNG stream (weed draws are consumed per empty tile
   even at zero chance, and feed the shop-unlock choice) via the CPython-exact
   Python_random.

   Nothing here is trusted for research until the Phase 4 bulk differential gate passes.
   The API shape is the game plan's: initial_state / step / observe / copy / run_game over
   flat mutable state. Allocation inside [step] is tolerated where faithfulness needs it;
   the game plan defers performance-specific layouts until the scalar model passes the
   gate. *)

(* Item tables. Indices are the state encoding; the string names live in Kag_serialize,
   which owns every conversion to the upstream JSON shapes. Order matches upstream
   PRODUCTS / CROPS / ANIMALS declaration order. *)
let product_count = 9 (* WHEAT CARROT TOMATO STRAWBERRY MELON EGG MILK WOOL FERTILIZER *)
let crop_count = 5 (* the first five products *)
let animal_count = 3 (* GOOSE COW SHEEP *)

(* The shed holds products and (boxed) animals: indices 0..8 are products, 9..11 are
   animals. *)
let shed_item_count = product_count + animal_count
let shed_index_of_animal animal = product_count + animal

(* Crop data (upstream CROPS), indexed WHEAT CARROT TOMATO STRAWBERRY MELON. *)
let crop_first_yield_day = [| 2; 2; 8; 10; 10 |]
let crop_max_yield_day = [| 4; 3; 8; 10; 12 |]
let crop_interval = [| 0; 0; 1; 2; 0 |]
let crop_max_yield = [| 6; 4; 4; 4; 6 |]
let crop_ongoing = [| false; false; true; true; false |]

(* Seed prices (CROPS[*]["seed"]) and animal prices (ANIMALS[*]["cost"]) are flat
   constants upstream, independent of the market curves. *)
let seed_costs = [| 10; 20; 50; 100; 80 |]
let animal_costs = [| 300; 400; 500 |]

(* Animal data (upstream ANIMALS), indexed GOOSE COW SHEEP. Structures are 0 = COOP, 1 =
   PASTURE; products are shed-item indices (EGG MILK WOOL). *)
let animal_structure = [| 0; 1; 1 |]
let animal_first_yield_day = [| 4; 8; 6 |]
let animal_interval = [| 1; 2; 3 |]
let animal_max_held = [| 4; 6; 6 |]
let animal_product = [| 5; 6; 7 |]

(* Every default I0 is MARKET_I0; per-item I0 arrives with marketParams support. Base
   prices in product order. *)
let market_i0 = 10_000
let market_base_prices = [| 25; 35; 60; 120; 250; 50; 160; 200; 100 |]

(* Pricing model (upstream MARKET_PARAMS): price(inv) = base + sign * amp * f(|inv - I0|),
   amp = target * base / f(T) floored at 1 after Python's round-half-even. *)
type shape =
  | Linear
  | Sq
  | Sqrt
  | Log
  | Log10
  | Hinge

(* One product's resolved price curve. [marketParams] configuration overrides are resolved
   into these before the model sees them; base/I0/T are integers as upstream declares
   them, targets travel as floats (integer-valued overrides are numerically identical in
   the double math). *)
type market_curve =
  { base : int
  ; i0 : int
  ; t : int
  ; below_func : shape
  ; below_target : float
  ; above_func : shape
  ; above_target : float
  }

let default_market_curves =
  let t = [| 400; 450; 200; 100; 300; 332; 122; 105; 200 |] in
  let below_func = [| Sqrt; Hinge; Hinge; Sqrt; Log; Hinge; Sqrt; Log; Linear |] in
  let below_target = [| 0.80; 1.00; 0.40; 0.70; 0.20; 0.40; 0.60; 0.20; 0.40 |] in
  let above_func = [| Log; Sqrt; Sqrt; Linear; Sq; Log; Linear; Sq; Linear |] in
  let above_target = [| 0.20; 0.70; 0.60; 1.60; 3.60; 0.20; 1.60; 3.20; 0.40 |] in
  Array.init product_count (fun item ->
    { base = market_base_prices.(item)
    ; i0 = market_i0
    ; t = t.(item)
    ; below_func = below_func.(item)
    ; below_target = below_target.(item)
    ; above_func = above_func.(item)
    ; above_target = above_target.(item)
    })
;;

let hinge_gain = 8.0
let price_floor = 1

(* Quadrants unlock in the fixed order NW, NE, SW, SE; a farm's [unlocked_quadrants] count
   is the whole story. *)
let quadrant_count = 4
let land_prices = [| 1000; 2000; 4000 |]
let max_shop_instances = 8

(* Shop product lists (upstream SHOPS), indexed in sorted-name order — the order
   rng.choice draws from: BAKERY BRUNCH_SPOT FARMERS_MARKET ICE_CREAM_SHOP PET_CAFE
   PIZZA_SHOP SMOOTHIE_SHOP YARN_STORE. Values are product indices; a single-product shop
   consumes double. *)
let shop_products =
  [| [| 5; 0 |] (* BAKERY: EGG WHEAT *)
   ; [| 5; 0; 3 |] (* BRUNCH_SPOT: EGG WHEAT STRAWBERRY *)
   ; [| 0; 1; 2; 3 |] (* FARMERS_MARKET: WHEAT CARROT TOMATO STRAWBERRY *)
   ; [| 3; 6; 0 |] (* ICE_CREAM_SHOP: STRAWBERRY MILK WHEAT *)
   ; [| 1 |] (* PET_CAFE: CARROT *)
   ; [| 6; 2; 0 |] (* PIZZA_SHOP: MILK TOMATO WHEAT *)
   ; [| 3; 6 |] (* SMOOTHIE_SHOP: STRAWBERRY MILK *)
   ; [| 7 |] (* YARN_STORE: WOOL *)
  |]
;;

let shop_indices = Array.init (Array.length shop_products) Fun.id
let player_count = 2

type status =
  | Active
  | Done

(* Field names and semantics mirror the upstream plant dict exactly; the serializer emits
   them 1:1. *)
type plant =
  { crop : int
  ; planted_day : int
  ; mutable watered_today : bool
  ; mutable consecutive_unwatered : int (* planting day counts as unwatered *)
  ; mutable yield_units : int
  ; mutable max_lifespan_step : int (* -1 for ongoing crops until final production *)
  ; mutable fertilized_until_day : int (* -1 when never fertilized *)
  }

(* Field names and semantics mirror the upstream animal dict exactly. *)
type animal_state =
  { animal : int
  ; placed_day : int
  ; mutable yield_units : int
  ; mutable consecutive_unfed : int
  ; mutable fed_today : bool
  ; mutable cared_today : bool
  ; mutable fertilizer_available : bool
  ; mutable pending_care_bonus : int
  }

type tile =
  | Empty
  | Locked
  | Weed
  | Plant of plant
  | Structure of int (* an empty coop/pasture; 0 = COOP, 1 = PASTURE *)
  | Animal of animal_state (* upstream folds the structure kind into the dict *)

(* The knobs from the upstream specification (kaggriculture.json), minus the
   framework-only [actTimeout]. [seed] is required here: differential replay always pins
   it, and the upstream null-seed randomization is a framework concern. [market_params]
   carries resolved per-item curves — sparse marketParams overrides are merged onto the
   defaults by whoever builds the config (None means pure defaults). *)
type config =
  { episode_steps : int
  ; turns_per_day : int
  ; board_size : int
  ; starting_money : int
  ; shed_capacity : int
  ; max_market_orders_per_turn : int
  ; farm_hand_cost_mult : int
  ; weed_spawn_chance : float
  ; town_shop_unlock_interval : int
  ; town_shop_sell_interval : int
  ; town_center_sell_interval : int
  ; seed : int
  ; market_params : market_curve array option
  }

let default_config =
  { episode_steps = 720
  ; turns_per_day = 24
  ; board_size = 10
  ; starting_money = 3000
  ; shed_capacity = 100
  ; max_market_orders_per_turn = 10
  ; farm_hand_cost_mult = 1
  ; weed_spawn_chance = 0.005
  ; town_shop_unlock_interval = 3
  ; town_shop_sell_interval = 4
  ; town_center_sell_interval = 24
  ; seed = 0
  ; market_params = None
  }
;;

type farm =
  { mutable money : float (* Python float upstream; kept identical *)
  ; mutable farmer_x : int
  ; mutable farmer_y : int
  ; mutable hands : (int * int) array (* replaced wholesale on hire / day reset *)
  ; mutable unlocked_quadrants : int (* 1..4, prefix of NW NE SW SE *)
  ; mutable hires_today : int
  ; tiles : tile array (* row-major: tiles.(y * board_size + x) *)
  }

(* Upstream inventories are Python dicts, and their insertion order is semantically
   observable: DROP and the nightly shed drop walk items in insertion order, so when shed
   room runs out, first-inserted items win and the rest are discarded. [order] mirrors
   that key order — it lists the items with nonzero count, oldest first. Zero-count items
   never appear (upstream deletes keys the moment they reach zero). *)
type inventory =
  { counts : int array (* shed_item_count *)
  ; mutable order : int array
  }

type private_state =
  { shed : int array
      (* shed_item_count; upstream keeps every key present from initialization onward, so
         a plain array in table order is exact *)
  ; seeds : int array (* crop_count; consumed directly by PLANT *)
  ; mutable inventories : inventory array
  (* slot 0 is the main farmer, slots 1+ are hands; always exactly 1 + Array.length hands
     entries *)
  }

type state =
  { config : config
  ; resolved_seed : int
  ; farms : farm array
  ; privates : private_state array
  ; market_curves : market_curve array (* resolved; defaults unless overridden *)
  ; market_inventory : int array (* product_count *)
  ; market_prices : int array (* product_count *)
  ; town_shops : int array (* shop table indices; prefix town_shop_count valid *)
  ; mutable town_shop_count : int
  ; mutable transitions : int
  ; mutable day : int
  ; mutable hour : int
  ; mutable status : status
  }

(* ---------------- actions ---------------- *)

type direction =
  | North
  | South
  | East
  | West

(* The unit operations implemented so far. Upstream treats malformed or inapplicable
   actions as silent no-ops; this typed surface cannot express malformed ones — mapping
   raw JSON tapes (including deliberately malformed fuzz) onto it is the differential
   runner's job. *)
type unit_op =
  | Unit_pass
  | Move of direction
  | Drop
  | Pickup of
      { item : int (* shed item index *)
      ; count : int
      }
  | Place of
      { item : int
      ; count : int
      }
  | Plant_crop of { crop : int }
  | Water
  | Harvest
  | Fertilize
  | Dig
  | Build_coop
  | Build_pasture
  | Feed
  | Care
  | Collect_fertilizer

type market_order =
  | Hire
  | Buy_seed of
      { crop : int
      ; count : int
      }
  | Buy_animal of
      { animal : int
      ; count : int
      }
  | Sell of
      { item : int (* any product *)
      ; count : int
      }
  | Buy_product of
      { item : int (* upstream allows only WHEAT and FERTILIZER *)
      ; count : int
      }
  | Buy_land
  (* An order slot upstream counts but cannot execute: _parse_order returned None (bad
     arity, non-integer or non-positive count, unrecognized op) or the lockstep's quote
     phase hit its "malformed sub-op; abort" branch (an item outside the op's domain).
     Both leave the player idle for that slot while still occupying it, so the slot must
     survive parsing — dropping it would shorten max_len and skip a price refresh. *)
  | Bad_order

type player_action =
  { farmer : unit_op
  ; hands : unit_op array (* entries beyond the current hand count are ignored *)
  ; market : market_order array (* truncated to maxMarketOrdersPerTurn *)
  }

let pass_action = { farmer = Unit_pass; hands = [||]; market = [||] }

(* A zero-copy read-only view: the arrays alias live state, which is what a rollout policy
   wants. [obs_step] follows the framework convention of resetting to zero once the
   episode is done. *)
type observation =
  { obs_player : int
  ; obs_step : int
  ; obs_day : int
  ; obs_hour : int
  ; obs_status : status
  ; obs_farms : farm array
  ; obs_private : private_state
  ; obs_market_inventory : int array
  ; obs_market_prices : int array
  ; obs_town_shops : int array
  ; obs_town_shop_count : int
  }

let quadrant_of ~board_size x y =
  let half = board_size / 2 in
  match y < half, x < half with
  | true, true -> 0 (* NW *)
  | true, false -> 1 (* NE *)
  | false, true -> 2 (* SW *)
  | false, false -> 3 (* SE *)
;;

(* Upstream spawns on the first shed-access tile inside NW, which is always the inner
   corner (half-1, half-1). *)
let default_spawn ~board_size =
  let half = board_size / 2 in
  half - 1, half - 1
;;

(* The four inner-corner tiles around the shed, in the NWSE order upstream uses for spawn
   preference and hire tie-breaking. *)
let shed_access_tiles ~board_size =
  let half = board_size / 2 in
  [| half - 1, half - 1; half, half - 1; half - 1, half; half, half |]
;;

let is_shed_adjacent ~board_size x y =
  let half = board_size / 2 in
  (x = half - 1 || x = half) && (y = half - 1 || y = half)
;;

let new_inventory () = { counts = Array.make shed_item_count 0; order = [||] }

let initial_state config =
  if config.episode_steps < 2 then invalid_arg "episodeSteps must be at least 2";
  if config.board_size < 4
  then invalid_arg "boardSize below the specification minimum of 4";
  (* Upstream clamps these with max(1, ...) at every use site; normalizing once here keeps
     the stored config equal to the effective one. *)
  let clamp1 value = max 1 value in
  let config =
    { config with
      turns_per_day = clamp1 config.turns_per_day
    ; max_market_orders_per_turn = clamp1 config.max_market_orders_per_turn
    ; town_shop_unlock_interval = clamp1 config.town_shop_unlock_interval
    ; town_shop_sell_interval = clamp1 config.town_shop_sell_interval
    ; town_center_sell_interval = clamp1 config.town_center_sell_interval
    }
  in
  let board_size = config.board_size in
  let spawn_x, spawn_y = default_spawn ~board_size in
  let farm () =
    { money = float_of_int config.starting_money
    ; farmer_x = spawn_x
    ; farmer_y = spawn_y
    ; hands = [||]
    ; unlocked_quadrants = 1
    ; hires_today = 0
    ; tiles =
        Array.init (board_size * board_size) (fun index ->
          let x = index mod board_size
          and y = index / board_size in
          if quadrant_of ~board_size x y = 0 then Empty else Locked)
    }
  in
  let private_state () =
    { shed = Array.make shed_item_count 0
    ; seeds = Array.make crop_count 0
    ; inventories = [| new_inventory () |]
    }
  in
  let market_curves =
    match config.market_params with
    | None -> default_market_curves
    | Some curves ->
      if Array.length curves <> product_count
      then invalid_arg "market_params must resolve every product";
      curves
  in
  { config
  ; resolved_seed = config.seed
  ; farms = Array.init player_count (fun _ -> farm ())
  ; privates = Array.init player_count (fun _ -> private_state ())
  ; market_curves
  ; market_inventory = Array.map (fun curve -> curve.i0) market_curves
  ; market_prices = Array.map (fun curve -> curve.base) market_curves
  ; town_shops = Array.make max_shop_instances 0
  ; town_shop_count = 0
  ; transitions = 0
  ; day = 0
  ; hour = 0
  ; status = Active
  }
;;

let copy_inventory inventory =
  { counts = Array.copy inventory.counts; order = Array.copy inventory.order }
;;

let copy state =
  { state with
    farms =
      Array.map
        (fun (farm : farm) ->
          { farm with
            tiles =
              Array.map
                (function
                  (* [with] on an unchanged field just forces a fresh record *)
                  | Plant plant -> Plant { plant with crop = plant.crop }
                  | Animal animal -> Animal { animal with animal = animal.animal }
                  | tile -> tile)
                farm.tiles
          ; hands = Array.copy farm.hands
          })
        state.farms
  ; privates =
      Array.map
        (fun p ->
          { shed = Array.copy p.shed
          ; seeds = Array.copy p.seeds
          ; inventories = Array.map copy_inventory p.inventories
          })
        state.privates
  ; market_inventory = Array.copy state.market_inventory
  ; market_prices = Array.copy state.market_prices
  ; town_shops = Array.copy state.town_shops
  }
;;

(* ---------------- inventory and shed helpers ---------------- *)

let shed_total shed = Array.fold_left ( + ) 0 shed

let inventory_add inventory item n =
  (* n > 0 by every caller; a fresh key appends to the insertion order, matching Python
     dict assignment. *)
  if inventory.counts.(item) = 0
  then inventory.order <- Array.append inventory.order [| item |];
  inventory.counts.(item) <- inventory.counts.(item) + n
;;

let inventory_remove inventory item n =
  (* n <= current count by every caller; deletion at zero removes the key. *)
  inventory.counts.(item) <- inventory.counts.(item) - n;
  if inventory.counts.(item) = 0
  then
    inventory.order
    <- Array.of_list (List.filter (( <> ) item) (Array.to_list inventory.order))
;;

let inventory_clear inventory =
  Array.fill inventory.counts 0 shed_item_count 0;
  inventory.order <- [||]
;;

(* Empty every item of [inventory] into [shed] in insertion order, respecting [capacity];
   overflow is discarded with the inventory (upstream DROP and the nightly drop share this
   exact shape). *)
let drop_inventory_to_shed inventory shed ~capacity =
  Array.iter
    (fun item ->
      let n = inventory.counts.(item) in
      let room = max 0 (capacity - shed_total shed) in
      let take = min n room in
      if take > 0 then shed.(item) <- shed.(item) + take)
    inventory.order;
  inventory_clear inventory
;;

(* ---------------- unit actions ---------------- *)

let fertilizer_item = 8 (* FERTILIZER's shed index *)

let new_plant ~crop ~day ~turns_per_day =
  { crop
  ; planted_day = day
  ; watered_today = false
  ; consecutive_unwatered = 1 (* planting day counts as unwatered *)
  ; yield_units = (if crop_ongoing.(crop) then 0 else 1)
  ; max_lifespan_step =
      (if crop_ongoing.(crop)
       then -1
       else (day + crop_max_yield_day.(crop) + 1) * turns_per_day)
  ; fertilized_until_day = -1
  }
;;

let apply_unit_action state ~player ~unit op =
  let farm = state.farms.(player) in
  let private_state = state.privates.(player) in
  let board_size = state.config.board_size in
  let capacity = state.config.shed_capacity in
  let position =
    if unit = 0
    then Some (farm.farmer_x, farm.farmer_y)
    else if unit - 1 < Array.length farm.hands
    then Some farm.hands.(unit - 1)
    else None (* extra hand actions are silently ignored, as upstream *)
  in
  match position with
  | None -> ()
  | Some (x, y) ->
    let set_position nx ny =
      if unit = 0
      then (
        farm.farmer_x <- nx;
        farm.farmer_y <- ny)
      else farm.hands.(unit - 1) <- nx, ny
    in
    let inventory = private_state.inventories.(unit) in
    (match op with
     | Unit_pass -> ()
     | Move direction ->
       let dx, dy =
         match direction with
         | North -> 0, -1
         | South -> 0, 1
         | East -> 1, 0
         | West -> -1, 0
       in
       let nx = x + dx
       and ny = y + dy in
       (* Movement onto Locked tiles is allowed upstream: a hand can spawn on a locked
          shed-access tile, and blocking movement would strand it. *)
       if nx >= 0 && nx < board_size && ny >= 0 && ny < board_size then set_position nx ny
     | Drop ->
       if is_shed_adjacent ~board_size x y
       then drop_inventory_to_shed inventory private_state.shed ~capacity
     | Pickup { item; count } ->
       if is_shed_adjacent ~board_size x y && count > 0
       then (
         let n = min count private_state.shed.(item) in
         if n > 0
         then (
           private_state.shed.(item) <- private_state.shed.(item) - n;
           inventory_add inventory item n))
     | Place { item; count } ->
       (* Animal placement first: standing on a matching unoccupied structure. Upstream
          returns from this branch whether or not the inventory take succeeds — it never
          falls through to the shed path from a matching structure. Otherwise, shed drop
          under adjacency and capacity. *)
       let tile = farm.tiles.((y * board_size) + x) in
       (match tile with
        | Structure kind
          when item >= product_count && kind = animal_structure.(item - product_count) ->
          if inventory.counts.(item) > 0
          then (
            inventory_remove inventory item 1;
            farm.tiles.((y * board_size) + x)
            <- Animal
                 { animal = item - product_count
                 ; placed_day = state.day
                 ; yield_units = 0
                 ; consecutive_unfed = 0
                 ; fed_today = false
                 ; cared_today = false
                 ; fertilizer_available = false
                 ; pending_care_bonus = 0
                 })
        | _ ->
          if is_shed_adjacent ~board_size x y && count > 0
          then (
            let n = min count inventory.counts.(item) in
            if n > 0
            then (
              let room = max 0 (capacity - shed_total private_state.shed) in
              let n = min n room in
              if n > 0
              then (
                inventory_remove inventory item n;
                private_state.shed.(item) <- private_state.shed.(item) + n))))
     | Plant_crop _
     | Water
     | Harvest
     | Fertilize
     | Dig
     | Build_coop
     | Build_pasture
     | Feed
     | Care
     | Collect_fertilizer ->
       (* Everything below mutates the tile the unit stands on, so upstream requires that
          tile to be owned. state.day equals upstream's pre-turn day (the transition
          counter has not been incremented yet). *)
       let tile_index = (y * board_size) + x in
       let tile = farm.tiles.(tile_index) in
       let day = state.day in
       if tile <> Locked
       then (
         match op, tile with
         | Plant_crop { crop }, Empty ->
           if private_state.seeds.(crop) > 0
           then (
             private_state.seeds.(crop) <- private_state.seeds.(crop) - 1;
             farm.tiles.(tile_index)
             <- Plant (new_plant ~crop ~day ~turns_per_day:state.config.turns_per_day))
         | Plant_crop _, _ -> ()
         | Water, Plant plant ->
           if not plant.watered_today
           then (
             plant.watered_today <- true;
             if not crop_ongoing.(plant.crop)
             then (
               let age_days = day - plant.planted_day in
               let window_start = (crop_max_yield_day.(plant.crop) + 1) / 2 in
               if window_start <= age_days && age_days <= crop_max_yield_day.(plant.crop)
               then (
                 let bonus = if plant.fertilized_until_day >= day then 2 else 1 in
                 plant.yield_units
                 <- min crop_max_yield.(plant.crop) (plant.yield_units + bonus))))
         | Water, _ -> ()
         | Harvest, Plant plant ->
           if plant.yield_units > 0
              && day - plant.planted_day >= crop_first_yield_day.(plant.crop)
           then (
             let units = plant.yield_units in
             plant.yield_units <- 0;
             inventory_add inventory plant.crop units;
             if not crop_ongoing.(plant.crop) then farm.tiles.(tile_index) <- Empty)
         | Harvest, Animal animal ->
           if animal.yield_units > 0
           then (
             let units = animal.yield_units in
             animal.yield_units <- 0;
             inventory_add inventory animal_product.(animal.animal) units)
         | Harvest, _ -> ()
         | Fertilize, Plant plant ->
           if inventory.counts.(fertilizer_item) > 0
           then (
             inventory_remove inventory fertilizer_item 1;
             (* Active for day, day+1, day+2 (3 days inclusive). *)
             plant.fertilized_until_day <- max plant.fertilized_until_day (day + 2))
         | Fertilize, _ -> ()
         | Dig, (Weed | Plant _ | Structure _) ->
           (* Removes plants, weeds, and empty structures; never a placed animal. *)
           farm.tiles.(tile_index) <- Empty
         | Dig, _ -> ()
         | Build_coop, Empty -> farm.tiles.(tile_index) <- Structure 0
         | Build_coop, _ -> ()
         | Build_pasture, Empty -> farm.tiles.(tile_index) <- Structure 1
         | Build_pasture, _ -> ()
         | Feed, Animal animal ->
           if (not animal.fed_today) && inventory.counts.(0) > 0 (* WHEAT *)
           then (
             inventory_remove inventory 0 1;
             animal.fed_today <- true)
         | Feed, _ -> ()
         | Care, Animal animal -> animal.cared_today <- true
         | Care, _ -> ()
         | Collect_fertilizer, Animal animal ->
           if animal.fertilizer_available
           then (
             animal.fertilizer_available <- false;
             inventory_add inventory fertilizer_item 1)
         | Collect_fertilizer, _ -> ()
         | (Unit_pass | Move _ | Drop | Pickup _ | Place _), _ ->
           assert false (* handled by the outer match *)))
;;

(* ---------------- market pricing ---------------- *)

let shape_value func x ~t =
  let x = Float.max 0.0 x in
  match func with
  | Linear -> x
  | Sq -> x *. x
  | Sqrt -> sqrt x
  | Log -> log (1.0 +. x)
  | Log10 -> log10 (1.0 +. x)
  | Hinge ->
    (* Degenerates to linear if T is missing or non-positive. *)
    if t <= 0.0
    then x
    else (
      let u = x /. t in
      u +. (hinge_gain *. (Float.max 0.0 (u -. 1.0) ** 2.0)))
;;

(* CPython's round(float): nearest integer, ties to even. *)
let python_round x =
  let floor_x = floor x in
  let diff = x -. floor_x in
  if diff > 0.5
  then floor_x +. 1.0
  else if diff < 0.5
  then floor_x
  else if Float.rem floor_x 2.0 = 0.0
  then floor_x
  else floor_x +. 1.0
;;

let market_price state item ~inventory =
  let curve = state.market_curves.(item) in
  let base = float_of_int curve.base in
  let i0 = curve.i0 in
  let t = float_of_int curve.t in
  let price =
    if inventory < i0
    then (
      let func = curve.below_func in
      let amp = curve.below_target *. base /. shape_value func t ~t in
      base +. (amp *. shape_value func (float_of_int (i0 - inventory)) ~t))
    else (
      let func = curve.above_func in
      let amp = curve.above_target *. base /. shape_value func t ~t in
      base -. (amp *. shape_value func (float_of_int (inventory - i0)) ~t))
  in
  max price_floor (int_of_float (python_round price))
;;

let refresh_prices state =
  for item = 0 to product_count - 1 do
    state.market_prices.(item)
    <- market_price state item ~inventory:state.market_inventory.(item)
  done
;;

(* ---------------- market orders ---------------- *)

(* Indexed so fib 0 = 1, fib 1 = 1, fib 2 = 2, fib 3 = 3, fib 4 = 5 ... *)
let fib n =
  let a = ref 1
  and b = ref 1 in
  for _ = 1 to n do
    let next = !a + !b in
    a := !b;
    b := next
  done;
  !a
;;

let hire_cost ~hires_today ~mult = mult * fib hires_today

(* First free shed-access tile in NWSE order; ties broken by minimum occupancy (farmer
   plus existing hands). *)
let spawn_hand farm ~board_size =
  let access = shed_access_tiles ~board_size in
  let occupancy = Array.make 4 0 in
  let count_occupant (x, y) =
    Array.iteri
      (fun index (ax, ay) ->
        if x = ax && y = ay then occupancy.(index) <- occupancy.(index) + 1)
      access
  in
  count_occupant (farm.farmer_x, farm.farmer_y);
  Array.iter count_occupant farm.hands;
  let best = ref 0 in
  for index = 1 to 3 do
    if occupancy.(index) < occupancy.(!best) then best := index
  done;
  access.(!best)
;;

let do_buy_land state ~player =
  let farm = state.farms.(player) in
  let extra_unlocked = farm.unlocked_quadrants - 1 in
  if extra_unlocked < Array.length land_prices
  then (
    let cost = land_prices.(extra_unlocked) in
    if farm.money >= float_of_int cost
    then (
      farm.money <- farm.money -. float_of_int cost;
      (* LAND_ORDER is NE, SW, SE — quadrant indices 1, 2, 3 in order. *)
      let quadrant = extra_unlocked + 1 in
      farm.unlocked_quadrants <- farm.unlocked_quadrants + 1;
      let board_size = state.config.board_size in
      Array.iteri
        (fun index tile ->
          if tile = Locked
             && quadrant_of ~board_size (index mod board_size) (index / board_size)
                = quadrant
          then farm.tiles.(index) <- Empty)
        farm.tiles))
;;

let do_hire state ~player =
  let farm = state.farms.(player) in
  let private_state = state.privates.(player) in
  let cost =
    hire_cost ~hires_today:farm.hires_today ~mult:state.config.farm_hand_cost_mult
  in
  if farm.money >= float_of_int cost
  then (
    farm.money <- farm.money -. float_of_int cost;
    farm.hires_today <- farm.hires_today + 1;
    farm.hands
    <- Array.append farm.hands [| spawn_hand farm ~board_size:state.config.board_size |];
    private_state.inventories
    <- Array.append private_state.inventories [| new_inventory () |])
;;

(* Per-unit lockstep, exactly upstream's two phases per unit: quote both players' current
   orders from the same pre-commit market inventory, then commit both in player order.
   Money or stock exhaustion aborts an order at exactly the same unit as upstream; prices
   refresh after every order index. *)
let process_market state (actions : player_action array) =
  let max_orders = state.config.max_market_orders_per_turn in
  let queues =
    Array.map
      (fun action ->
        Array.sub action.market 0 (min max_orders (Array.length action.market)))
      actions
  in
  let max_len = Array.fold_left (fun acc q -> max acc (Array.length q)) 0 queues in
  let kinds = Array.make player_count None in
  let remaining = Array.make player_count 0 in
  let quoted_price = Array.make player_count 0 in
  for index = 0 to max_len - 1 do
    Array.iteri
      (fun player queue ->
        kinds.(player) <- None;
        if index < Array.length queue
        then (
          match queue.(index) with
          | Hire ->
            (* Atomic orders resolve once, in player order, before lockstep. *)
            do_hire state ~player
          | Buy_land -> do_buy_land state ~player
          | Bad_order -> () (* occupies the slot, does nothing *)
          | Buy_seed { count; _ }
          | Buy_animal { count; _ }
          | Sell { count; _ }
          | Buy_product { count; _ } ->
            (* Upstream _parse_order rejects n <= 0 as malformed. *)
            if count > 0
            then (
              kinds.(player) <- Some queue.(index);
              remaining.(player) <- count)))
      queues;
    let progressing = ref true in
    (* Upstream's runaway guard: the lockstep body runs at most 99,999 times per order
       index, then aborts the index outright. Reachable only when one order can commit
       that many units — a cheap seed against a very large bankroll. *)
    let iterations = ref 0 in
    while !progressing && !iterations < 99_999 do
      incr iterations;
      (* Quote phase: both players see the same pre-commit inventory. *)
      let quoted_any = ref false in
      for player = 0 to player_count - 1 do
        match kinds.(player) with
        | None -> ()
        | Some _ when remaining.(player) <= 0 -> ()
        | Some order ->
          quoted_any := true;
          quoted_price.(player)
          <- (match order with
              | Hire | Buy_land | Bad_order ->
                0 (* unreachable: never entered into [kinds] *)
              | Buy_seed { crop; _ } -> seed_costs.(crop)
              | Buy_animal { animal; _ } -> animal_costs.(animal)
              | Sell { item; _ } ->
                market_price state item ~inventory:state.market_inventory.(item)
              | Buy_product { item; _ } ->
                (* Quoted at post-buy inventory so a buy/sell round-trip against an
                   unchanged market nets zero. *)
                market_price state item ~inventory:(state.market_inventory.(item) - 1))
      done;
      if not !quoted_any
      then progressing := false
      else (
        (* Commit phase, in player order. *)
        let committed_any = ref false in
        for player = 0 to player_count - 1 do
          match kinds.(player) with
          | None -> ()
          | Some _ when remaining.(player) <= 0 -> ()
          | Some order ->
            let farm = state.farms.(player) in
            let private_state = state.privates.(player) in
            let price = quoted_price.(player) in
            let ok =
              match order with
              | Hire | Buy_land | Bad_order -> false
              | Buy_seed { crop; _ } ->
                if farm.money >= float_of_int price
                then (
                  farm.money <- farm.money -. float_of_int price;
                  private_state.seeds.(crop) <- private_state.seeds.(crop) + 1;
                  true)
                else false
              | Buy_animal { animal; _ } ->
                if farm.money >= float_of_int price
                   && shed_total private_state.shed < state.config.shed_capacity
                then (
                  farm.money <- farm.money -. float_of_int price;
                  let item = shed_index_of_animal animal in
                  private_state.shed.(item) <- private_state.shed.(item) + 1;
                  true)
                else false
              | Sell { item; _ } ->
                if private_state.shed.(item) <= 0
                then false
                else (
                  private_state.shed.(item) <- private_state.shed.(item) - 1;
                  farm.money <- farm.money +. float_of_int price;
                  (* Sales at $1 do not increase market supply. *)
                  if price > 1
                  then state.market_inventory.(item) <- state.market_inventory.(item) + 1;
                  true)
              | Buy_product { item; _ } ->
                (* Bought goods land in the shed, which obeys shedCapacity. *)
                if farm.money >= float_of_int price
                   && shed_total private_state.shed < state.config.shed_capacity
                then (
                  farm.money <- farm.money -. float_of_int price;
                  private_state.shed.(item) <- private_state.shed.(item) + 1;
                  state.market_inventory.(item) <- state.market_inventory.(item) - 1;
                  true)
                else false
            in
            if ok
            then (
              remaining.(player) <- remaining.(player) - 1;
              committed_any := true)
            else kinds.(player) <- None (* can't continue this order *)
        done;
        if not !committed_any then progressing := false)
    done;
    refresh_prices state
  done
;;

(* ---------------- decay and end of day ---------------- *)

(* Overripe decay, every turn: once past max_lifespan_step, a non-ongoing (or
   final-production ongoing) plant loses one unit every second step, then turns to weed.
   [step] is the pre-increment transition counter. *)
let decay_plants state ~step =
  Array.iter
    (fun (farm : farm) ->
      Array.iteri
        (fun index tile ->
          match tile with
          | Plant plant ->
            let mls = plant.max_lifespan_step in
            if mls >= 0 && step >= mls && (step - mls) mod 2 = 0
            then (
              plant.yield_units <- plant.yield_units - 1;
              if plant.yield_units <= 0 then farm.tiles.(index) <- Weed)
          | _ -> ())
        farm.tiles)
    state.farms
;;

(* The nightly plant pass: watering bookkeeping (two consecutive unwatered days -> weed),
   then the ongoing-crop production schedule. [day] is the day just ended. *)
let daily_refresh_plants farm ~day ~turns_per_day =
  let next_day = day + 1 in
  Array.iteri
    (fun index tile ->
      match tile with
      | Plant plant ->
        let was_watered = plant.watered_today in
        if was_watered
        then plant.consecutive_unwatered <- 0
        else plant.consecutive_unwatered <- plant.consecutive_unwatered + 1;
        plant.watered_today <- false;
        if plant.consecutive_unwatered >= 2
        then farm.tiles.(index) <- Weed
        else if crop_ongoing.(plant.crop)
        then (
          let days_since_first =
            next_day - plant.planted_day - crop_first_yield_day.(plant.crop)
          in
          let interval = crop_interval.(plant.crop) in
          if days_since_first >= 0 && days_since_first mod interval = 0
          then (
            let production_count = (days_since_first / interval) + 1 in
            if production_count <= crop_max_yield.(plant.crop)
            then (
              (* Fertilizer bonus only applies on watered days. *)
              let fertilized = was_watered && plant.fertilized_until_day >= day in
              plant.yield_units
              <- min
                   crop_max_yield.(plant.crop)
                   (plant.yield_units + if fertilized then 2 else 1);
              if production_count = crop_max_yield.(plant.crop)
              then plant.max_lifespan_step <- (next_day + 1) * turns_per_day)))
      | _ -> ())
    farm.tiles
;;

(* The nightly animal pass: feeding bookkeeping (two consecutive unfed days and the animal
   escapes, leaving its structure), then the production schedule with the care bonus, then
   the daily flag resets. *)
let daily_refresh_animals farm ~day =
  let next_day = day + 1 in
  Array.iteri
    (fun index tile ->
      match tile with
      | Animal animal ->
        if animal.fed_today
        then animal.consecutive_unfed <- 0
        else animal.consecutive_unfed <- animal.consecutive_unfed + 1;
        if animal.consecutive_unfed >= 2
        then farm.tiles.(index) <- Structure animal_structure.(animal.animal)
        else (
          let days_since_first =
            next_day - animal.placed_day - animal_first_yield_day.(animal.animal)
          in
          if days_since_first >= 0
             && days_since_first mod animal_interval.(animal.animal) = 0
          then (
            (* Care bonus only consumed on a fed production day. *)
            let bonus = if animal.fed_today then animal.pending_care_bonus else 0 in
            animal.yield_units
            <- min animal_max_held.(animal.animal) (animal.yield_units + 1 + bonus);
            animal.pending_care_bonus <- 0);
          if animal.cared_today && animal.fed_today
          then animal.pending_care_bonus <- animal.pending_care_bonus + 1;
          animal.fertilizer_available <- true;
          animal.fed_today <- false;
          animal.cared_today <- false)
      | _ -> ())
    farm.tiles
;;

let end_of_day state =
  let board_size = state.config.board_size in
  let spawn_x, spawn_y = default_spawn ~board_size in
  (* Stable RNG keyed off the resolved seed and the day, exactly upstream's
     random.Random((seed * 1_000_003) ^ day). One generator serves both players' weed
     draws and then the shop unlock, in that order. *)
  let rng = Python_random.create (state.resolved_seed * 1_000_003 lxor state.day) in
  for player = 0 to player_count - 1 do
    let farm = state.farms.(player) in
    let private_state = state.privates.(player) in
    daily_refresh_plants farm ~day:state.day ~turns_per_day:state.config.turns_per_day;
    daily_refresh_animals farm ~day:state.day;
    (* Weed spawn: one draw per empty tile in row-major order — the draws are consumed
       even when weedSpawnChance is 0, which matters for everything the generator serves
       afterwards. *)
    Array.iteri
      (fun index tile ->
        match tile with
        | Empty ->
          if Python_random.random rng < state.config.weed_spawn_chance
          then farm.tiles.(index) <- Weed
        | _ -> ())
      farm.tiles;
    Array.iter
      (fun inventory ->
        drop_inventory_to_shed
          inventory
          private_state.shed
          ~capacity:state.config.shed_capacity)
      private_state.inventories;
    farm.farmer_x <- spawn_x;
    farm.farmer_y <- spawn_y;
    farm.hands <- [||];
    farm.hires_today <- 0;
    private_state.inventories <- [| new_inventory () |]
  done;
  (* Shop unlock: drawn with replacement from the sorted shop table; the same shop can
     unlock repeatedly, capped at max_shop_instances total. *)
  let next_day = state.day + 1 in
  if next_day mod state.config.town_shop_unlock_interval = 0
     && state.town_shop_count < max_shop_instances
  then (
    state.town_shops.(state.town_shop_count) <- Python_random.choice rng shop_indices;
    state.town_shop_count <- state.town_shop_count + 1)
;;

(* ---------------- the transition ---------------- *)

let step state (action0 : player_action) (action1 : player_action) =
  if state.status = Done then invalid_arg "cannot step a completed game";
  let actions = [| action0; action1 |] in
  Array.iteri
    (fun player action ->
      (* Atomic PLANT validation: if this player's total PLANT requests for a crop (farmer
         plus every hand entry, existent or not) exceed the seeds on hand, ALL PLANT
         requests for that crop become PASS. *)
      let demand = Array.make crop_count 0 in
      let count_demand = function
        | Plant_crop { crop } -> demand.(crop) <- demand.(crop) + 1
        | _ -> ()
      in
      count_demand action.farmer;
      Array.iter count_demand action.hands;
      (* The blocked set is fixed before any unit acts, from the seed counts as they
         stand at the start of the turn. Re-reading the live count per unit would let an
         earlier unit's plant block a later one, which upstream never does: it builds
         [blocked] once and only then applies anything. [demand] is reused as the flag so
         the transition still allocates nothing beyond it. *)
      let seeds = state.privates.(player).seeds in
      for crop = 0 to crop_count - 1 do
        if demand.(crop) > seeds.(crop) then demand.(crop) <- -1
      done;
      let allowed = function
        | Plant_crop { crop } when demand.(crop) < 0 -> Unit_pass
        | op -> op
      in
      apply_unit_action state ~player ~unit:0 (allowed action.farmer);
      Array.iteri
        (fun hand_index op ->
          apply_unit_action state ~player ~unit:(hand_index + 1) (allowed op))
        action.hands)
    actions;
  process_market state actions;
  (* Town consumption (upstream _town_consume): unlocked-shop instances first (each
     instance consumes independently; single-product shops pull double), then the
     town-center tick, then a price refresh. *)
  if state.transitions mod state.config.town_shop_sell_interval = 0
  then
    for shop = 0 to state.town_shop_count - 1 do
      let products = shop_products.(state.town_shops.(shop)) in
      let multiplier = if Array.length products = 1 then 2 else 1 in
      Array.iter
        (fun item ->
          state.market_inventory.(item) <- state.market_inventory.(item) - multiplier)
        products
    done;
  if state.transitions mod state.config.town_center_sell_interval = 0
  then
    for item = 0 to product_count - 2 do
      (* every product except FERTILIZER *)
      state.market_inventory.(item) <- state.market_inventory.(item) - 1
    done;
  refresh_prices state;
  decay_plants state ~step:state.transitions;
  if (state.transitions + 1) mod state.config.turns_per_day = 0 then end_of_day state;
  state.transitions <- state.transitions + 1;
  state.day <- state.transitions / state.config.turns_per_day;
  state.hour <- state.transitions mod state.config.turns_per_day;
  (* The interpreter sees prior observation steps 0..episodeSteps-2. It marks DONE while
     processing episodeSteps-2, producing episodeSteps-1 transitions. *)
  if state.transitions >= state.config.episode_steps - 1 then state.status <- Done
;;

let observe state ~player =
  if player < 0 || player >= player_count then invalid_arg "player must be 0 or 1";
  { obs_player =
      player (* The framework zeroes the shared step counter once the episode is done. *)
  ; obs_step = (if state.status = Done then 0 else state.transitions)
  ; obs_day = state.day
  ; obs_hour = state.hour
  ; obs_status = state.status
  ; obs_farms = state.farms
  ; obs_private = state.privates.(player)
  ; obs_market_inventory = state.market_inventory
  ; obs_market_prices = state.market_prices
  ; obs_town_shops = state.town_shops
  ; obs_town_shop_count = state.town_shop_count
  }
;;

(* Upstream rewards stay 0 until the terminal turn assigns each player their bank balance. *)
let reward state ~player = if state.status = Done then state.farms.(player).money else 0.0

type result =
  { final_money : float array
  ; result_transitions : int
  }

(* The only policy the slice supports; a real policy type arrives once research needs one. *)
let run_game config =
  let state = initial_state config in
  while state.status = Active do
    step state pass_action pass_action
  done;
  { final_money = Array.map (fun farm -> farm.money) state.farms
  ; result_transitions = state.transitions
  }
;;
