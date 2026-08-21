(* Canonical JSON projections of the model state, matching the shapes the oracle adapter
   records (reference/oracle.py): [diagnostic] mirrors diagnostic_state, [observation]
   mirrors player_observation minus remainingOverageTime (framework timing, excluded from
   differential scope — the fixture recorder strips it too).

   This module owns every index→name conversion; the engine stores only integers and
   variants. Comparisons should go through [normalize] so key order never matters. *)

open Kag_model

(* Upstream declaration order (PRODUCTS); indices match Model's encoding. *)
let product_names =
  [| "WHEAT"
   ; "CARROT"
   ; "TOMATO"
   ; "STRAWBERRY"
   ; "MELON"
   ; "EGG"
   ; "MILK"
   ; "WOOL"
   ; "FERTILIZER"
  |]
;;

let animal_names = [| "GOOSE"; "COW"; "SHEEP" |]
let structure_names = [| "COOP"; "PASTURE" |]

(* Shed items are products then animals, matching Model.shed_item_count. *)
let shed_item_names = Array.append product_names animal_names
let quadrant_names = [| "NW"; "NE"; "SW"; "SE" |]

(* sorted(SHOPS) — the order rng.choice draws from at shop-unlock time. *)
let shop_names =
  [| "BAKERY"
   ; "BRUNCH_SPOT"
   ; "FARMERS_MARKET"
   ; "ICE_CREAM_SHOP"
   ; "PET_CAFE"
   ; "PIZZA_SHOP"
   ; "SMOOTHIE_SHOP"
   ; "YARN_STORE"
  |]
;;

let json_of_tile (tile : Model.tile) : Yojson.Safe.t =
  match tile with
  | Model.Empty -> `Null
  | Model.Locked -> `String "LOCKED"
  | Model.Weed -> `Assoc [ "kind", `String "WEED" ]
  | Model.Plant plant ->
    `Assoc
      [ "kind", `String "PLANT"
      ; "crop", `String product_names.(plant.Model.crop)
      ; "planted_day", `Int plant.Model.planted_day
      ; "watered_today", `Bool plant.Model.watered_today
      ; "consecutive_unwatered", `Int plant.Model.consecutive_unwatered
      ; "yield_units", `Int plant.Model.yield_units
      ; "max_lifespan_step", `Int plant.Model.max_lifespan_step
      ; "fertilized_until_day", `Int plant.Model.fertilized_until_day
      ]
  | Model.Structure kind -> `Assoc [ "kind", `String structure_names.(kind) ]
  | Model.Animal animal ->
    `Assoc
      [ "kind", `String structure_names.(Model.animal_structure.(animal.Model.animal))
      ; "animal", `String animal_names.(animal.Model.animal)
      ; "placed_day", `Int animal.Model.placed_day
      ; "yield_units", `Int animal.Model.yield_units
      ; "consecutive_unfed", `Int animal.Model.consecutive_unfed
      ; "fed_today", `Bool animal.Model.fed_today
      ; "cared_today", `Bool animal.Model.cared_today
      ; "fertilizer_available", `Bool animal.Model.fertilizer_available
      ; "pending_care_bonus", `Int animal.Model.pending_care_bonus
      ]
;;

let json_of_position x y : Yojson.Safe.t = `List [ `Int x; `Int y ]

let json_of_farm ~board_size (farm : Model.farm) : Yojson.Safe.t =
  let row y =
    `List
      (List.init board_size (fun x ->
         json_of_tile farm.Model.tiles.((y * board_size) + x)))
  in
  `Assoc
    [ "money", `Float farm.Model.money
    ; "tiles", `List (List.init board_size row)
    ; "farmer", json_of_position farm.Model.farmer_x farm.Model.farmer_y
    ; ( "hands"
      , `List
          (Array.to_list
             (Array.map (fun (x, y) -> json_of_position x y) farm.Model.hands)) )
    ; ( "unlocked_quadrants"
      , `List
          (List.init farm.Model.unlocked_quadrants (fun i -> `String quadrant_names.(i)))
      )
    ; "hires_today", `Int farm.Model.hires_today
    ]
;;

let json_of_counts names counts : Yojson.Safe.t =
  `Assoc (Array.to_list (Array.mapi (fun i name -> name, `Int counts.(i)) names))
;;

(* Upstream inventory dicts hold only nonzero entries (zero-count keys are deleted on the
   spot). Emitted in table order; comparisons normalize key order anyway. *)
let json_of_inventory (inventory : Model.inventory) : Yojson.Safe.t =
  let entries = ref [] in
  for i = Array.length inventory.Model.counts - 1 downto 0 do
    if inventory.Model.counts.(i) <> 0
    then entries := (shed_item_names.(i), `Int inventory.Model.counts.(i)) :: !entries
  done;
  `Assoc !entries
;;

let json_of_private (p : Model.private_state) : Yojson.Safe.t =
  `Assoc
    [ "shed", json_of_counts shed_item_names p.Model.shed
    ; "seeds", json_of_counts (Array.sub product_names 0 Model.crop_count) p.Model.seeds
    ; ( "inventories"
      , `List (Array.to_list (Array.map json_of_inventory p.Model.inventories)) )
    ]
;;

(* No "params" key: it appears upstream only under marketParams overrides, which the model
   deliberately cannot represent yet. *)
let json_of_market (state : Model.state) : Yojson.Safe.t =
  `Assoc
    [ "inventory", json_of_counts product_names state.Model.market_inventory
    ; "prices", json_of_counts product_names state.Model.market_prices
    ]
;;

let json_of_town (state : Model.state) : Yojson.Safe.t =
  `Assoc
    [ ( "unlocked_shops"
      , `List
          (List.init state.Model.town_shop_count (fun i ->
             `String shop_names.(state.Model.town_shops.(i)))) )
    ]
;;

let diagnostic (state : Model.state) : Yojson.Safe.t =
  let board_size = state.Model.config.Model.board_size in
  `Assoc
    [ ( "farms"
      , `List (Array.to_list (Array.map (json_of_farm ~board_size) state.Model.farms)) )
    ; "market", json_of_market state
    ; "town", json_of_town state
    ; "privates", `List (Array.to_list (Array.map json_of_private state.Model.privates))
    ; "day", `Int state.Model.day
    ; "hour", `Int state.Model.hour
    ; "resolved_seed", `Int state.Model.resolved_seed
    ]
;;

let observation (state : Model.state) ~player : Yojson.Safe.t =
  let obs = Model.observe state ~player in
  let board_size = state.Model.config.Model.board_size in
  `Assoc
    [ "player", `Int obs.Model.obs_player
    ; ( "farms"
      , `List (Array.to_list (Array.map (json_of_farm ~board_size) obs.Model.obs_farms)) )
    ; "private", json_of_private obs.Model.obs_private
    ; "market", json_of_market state
    ; "town", json_of_town state
    ; "day", `Int obs.Model.obs_day
    ; "hour", `Int obs.Model.obs_hour
    ; "step", `Int obs.Model.obs_step
    ]
;;

(* The group-2 per-turn comparison: everything mutable by the implemented rules except
   tiles (no implemented rule touches a tile, so tiles are compared at initialization and
   episode end only) and the market/town state (excluded until their rule groups land).
   The fixture recorder builds the same projection from the oracle's diagnostic state. *)
let turn_digest (state : Model.state) : Yojson.Safe.t =
  let nonzero_counts names counts : Yojson.Safe.t =
    let entries = ref [] in
    for i = Array.length names - 1 downto 0 do
      if counts.(i) <> 0 then entries := (names.(i), `Int counts.(i)) :: !entries
    done;
    `Assoc !entries
  in
  let board_size = state.Model.config.Model.board_size in
  let farm_digest (farm : Model.farm) : Yojson.Safe.t =
    (* Sparse row-major projection of every tile that is not Empty/Locked — harvests of
       non-ongoing crops and DIGs show up as absences. *)
    let tiles = ref [] in
    for index = Array.length farm.Model.tiles - 1 downto 0 do
      match farm.Model.tiles.(index) with
      | Model.Empty | Model.Locked -> ()
      | tile ->
        let x = index mod board_size
        and y = index / board_size in
        tiles := `List [ `Int x; `Int y; json_of_tile tile ] :: !tiles
    done;
    `Assoc
      [ "money", `Float farm.Model.money
      ; "farmer", json_of_position farm.Model.farmer_x farm.Model.farmer_y
      ; ( "hands"
        , `List
            (Array.to_list
               (Array.map (fun (x, y) -> json_of_position x y) farm.Model.hands)) )
      ; "hires_today", `Int farm.Model.hires_today
      ; ( "unlocked_quadrants"
        , `List
            (List.init farm.Model.unlocked_quadrants (fun i -> `String quadrant_names.(i)))
        )
      ; "tiles", `List !tiles
      ]
  in
  let private_digest (p : Model.private_state) : Yojson.Safe.t =
    `Assoc
      [ "shed", nonzero_counts shed_item_names p.Model.shed
      ; "seeds", nonzero_counts (Array.sub product_names 0 Model.crop_count) p.Model.seeds
      ; ( "inventories"
        , `List (Array.to_list (Array.map json_of_inventory p.Model.inventories)) )
      ]
  in
  `Assoc
    [ "farms", `List (Array.to_list (Array.map farm_digest state.Model.farms))
    ; "privates", `List (Array.to_list (Array.map private_digest state.Model.privates))
    ]
;;

(* ---------------- action tapes ---------------- *)

(* Map the upstream JSON action shapes onto the typed action surface. This is deliberately
   strict: an op or item the model does not implement yet fails loudly instead of
   no-opping, so a fixture tape cannot silently outrun the engine. Malformed-action
   tolerance is the Phase 4 fuzz runner's concern. *)

let index_of table name =
  let rec search i =
    if i >= Array.length table
    then failwith (Printf.sprintf "unknown item %S in action tape" name)
    else if table.(i) = name
    then i
    else search (i + 1)
  in
  search 0
;;

let unit_op_of_json (json : Yojson.Safe.t) : Model.unit_op =
  let fail () =
    failwith ("unsupported unit action in tape: " ^ Yojson.Safe.to_string json)
  in
  match json with
  | `List (`String op :: rest) ->
    (match op, rest with
     | "PASS", _ -> Model.Unit_pass
     | "NORTH", _ -> Model.Move Model.North
     | "SOUTH", _ -> Model.Move Model.South
     | "EAST", _ -> Model.Move Model.East
     | "WEST", _ -> Model.Move Model.West
     | "DROP", _ -> Model.Drop
     | "WATER", _ -> Model.Water
     | "HARVEST", _ -> Model.Harvest
     | "FERTILIZE", _ -> Model.Fertilize
     | "DIG", _ -> Model.Dig
     | "BUILD_COOP", _ -> Model.Build_coop
     | "BUILD_PASTURE", _ -> Model.Build_pasture
     | "FEED", _ -> Model.Feed
     | "CARE", _ -> Model.Care
     | "COLLECT_FERTILIZER", _ -> Model.Collect_fertilizer
     | "PLANT", `String crop :: _ ->
       let crop = index_of product_names crop in
       if crop >= Model.crop_count then fail ();
       Model.Plant_crop { crop }
     | "PICKUP", `String item :: rest ->
       let count =
         match rest with
         | `Int n :: _ -> n
         | [] -> 1
         | _ -> fail ()
       in
       Model.Pickup { item = index_of shed_item_names item; count }
     | "PLACE", `String item :: rest ->
       let count =
         match rest with
         | `Int n :: _ -> n
         | [] -> 1
         | _ -> fail ()
       in
       Model.Place { item = index_of shed_item_names item; count }
     | _ -> fail ())
  | _ -> fail ()
;;

let market_order_of_json (json : Yojson.Safe.t) : Model.market_order =
  let fail () =
    failwith ("unsupported market order in tape: " ^ Yojson.Safe.to_string json)
  in
  match json with
  | `List (`String "HIRE" :: _) -> Model.Hire
  | `List [ `String "BUY_SEED"; `String item; `Int count ] ->
    let crop = index_of product_names item in
    if crop >= Model.crop_count then fail ();
    Model.Buy_seed { crop; count }
  | `List [ `String "BUY_ANIMAL"; `String item; `Int count ] ->
    Model.Buy_animal { animal = index_of animal_names item; count }
  | `List [ `String "SELL"; `String item; `Int count ] ->
    Model.Sell { item = index_of product_names item; count }
  | `List [ `String "BUY_PRODUCT"; `String item; `Int count ] ->
    if item <> "WHEAT" && item <> "FERTILIZER" then fail ();
    Model.Buy_product { item = index_of product_names item; count }
  | `List (`String "BUY_LAND" :: _) -> Model.Buy_land
  | _ -> fail ()
;;

let shape_of_name = function
  | "linear" -> Model.Linear
  | "sq" -> Model.Sq
  | "sqrt" -> Model.Sqrt
  | "log" -> Model.Log
  | "log10" -> Model.Log10
  | "hinge" -> Model.Hinge
  | other -> failwith (Printf.sprintf "unknown market shape function %S" other)
;;

(* Parse a fully-resolved marketParams table (every product present, the upstream
   _resolve_market_params output) into per-item curves. *)
let market_curves_of_json (json : Yojson.Safe.t) : Model.market_curve array =
  let fields =
    match json with
    | `Assoc fields -> fields
    | _ -> failwith "marketParams must be an object"
  in
  let to_int name value =
    match value with
    | `Int i -> i
    | _ -> failwith (Printf.sprintf "marketParams %s must be an integer" name)
  in
  let to_float value =
    match value with
    | `Int i -> float_of_int i
    | `Float f -> f
    | _ -> failwith "marketParams target must be a number"
  in
  Array.map
    (fun product ->
      let entry =
        match List.assoc_opt product fields with
        | Some (`Assoc entry) -> entry
        | _ -> failwith (Printf.sprintf "marketParams missing product %s" product)
      in
      let get name =
        match List.assoc_opt name entry with
        | Some value -> value
        | None -> failwith (Printf.sprintf "marketParams %s missing %s" product name)
      in
      let shape name =
        match get name with
        | `String value -> shape_of_name value
        | _ -> failwith "marketParams shape function must be a string"
      in
      { Model.base = to_int "base" (get "base")
      ; i0 = to_int "I0" (get "I0")
      ; t = to_int "T" (get "T")
      ; below_func = shape "below_func"
      ; below_target = to_float (get "below_target")
      ; above_func = shape "above_func"
      ; above_target = to_float (get "above_target")
      })
    product_names
;;

let player_action_of_json (json : Yojson.Safe.t) : Model.player_action =
  let member name =
    match json with
    | `Assoc fields ->
      (try List.assoc name fields with
       | Not_found -> `List [])
    | _ -> failwith "player action tape entry must be an object"
  in
  let to_array f json =
    match json with
    | `List items -> Array.of_list (List.map f items)
    | _ -> failwith "expected a JSON list in action tape"
  in
  { Model.farmer = unit_op_of_json (member "farmer")
  ; hands = to_array unit_op_of_json (member "hands")
  ; market = to_array market_order_of_json (member "market")
  }
;;

(* Recursive key-sort so structural equality ignores object key order. *)
let rec normalize (json : Yojson.Safe.t) : Yojson.Safe.t =
  match json with
  | `Assoc fields ->
    `Assoc
      (List.sort
         (fun (a, _) (b, _) -> String.compare a b)
         (List.map (fun (key, value) -> key, normalize value) fields))
  | `List items -> `List (List.map normalize items)
  | other -> other
;;

(* First differing path between two normalized documents — the seed of the Phase 4
   divergence report. *)
let first_diff (a : Yojson.Safe.t) (b : Yojson.Safe.t) : string option =
  let shown json =
    let text = Yojson.Safe.to_string json in
    if String.length text > 120 then String.sub text 0 120 ^ "..." else text
  in
  let rec walk path a b =
    match a, b with
    | `Assoc xs, `Assoc ys ->
      let keys fields = List.map fst fields in
      if keys xs <> keys ys
      then
        Some
          (Printf.sprintf
             "%s: key sets differ (%s vs %s)"
             path
             (String.concat "," (keys xs))
             (String.concat "," (keys ys)))
      else
        List.fold_left2
          (fun acc (key, x) (_, y) ->
            match acc with
            | Some _ -> acc
            | None -> walk (path ^ "." ^ key) x y)
          None
          xs
          ys
    | `List xs, `List ys ->
      if List.length xs <> List.length ys
      then
        Some
          (Printf.sprintf
             "%s: lengths differ (%d vs %d)"
             path
             (List.length xs)
             (List.length ys))
      else
        List.fold_left2
          (fun (index, acc) x y ->
            match acc with
            | Some _ -> index + 1, acc
            | None -> index + 1, walk (Printf.sprintf "%s[%d]" path index) x y)
          (0, None)
          xs
          ys
        |> snd
    | _ ->
      if a = b
      then None
      else Some (Printf.sprintf "%s: %s vs %s" path (shown a) (shown b))
  in
  walk "$" (normalize a) (normalize b)
;;
