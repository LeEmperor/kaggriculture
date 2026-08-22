(* The Kaggriculture observation vocabulary over [Kag_model.Model.observation].

   This is the native counterpart of [Vocabulary]: the accessor names and kinds are
   identical, but values are read directly from the simulator's zero-copy observation
   instead of walking its JSON projection. String-to-index conversion stays here at the
   game seam; [Kag_model] deliberately stores only integer item indices. *)

open Policy_dsl.Expr
module Model = Kag_model.Model

exception Observation_error of string

let error fmt = Printf.ksprintf (fun message -> raise (Observation_error message)) fmt

(* Upstream declaration order. These restate the serializer-owned tables on purpose:
   serialization and policy vocabulary are separate boundaries around a string-free
   engine. Keep the full product/shed domains here even though the current family only
   names a crop, so future accessors and emits resolve against the same index space. *)
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
let shed_item_names = Array.append product_names animal_names

let index_of names name =
  let rec search index =
    if index = Array.length names
    then error "unknown item: %s" name
    else if names.(index) = name
    then index
    else search (index + 1)
  in
  search 0
;;

let product_index name = index_of product_names name
let shed_item_index name = index_of shed_item_names name

let crop_index_of_name name =
  let index = product_index name in
  if index < Model.crop_count then index else error "unknown crop: %s" name
;;

let crop_index parameters =
  match SM.find_opt "crop" parameters with
  | Some (Vstr crop) -> crop_index_of_name crop
  | _ -> error "the family's 'crop' parameter is missing or not a string"
;;

let own_farm (observation : Model.observation) =
  observation.obs_farms.(observation.obs_player)
;;

let current_tile (observation : Model.observation) =
  let farm = own_farm observation in
  farm.tiles.((farm.farmer_y * observation.obs_board_size) + farm.farmer_x)
;;

let plant_tile observation =
  match current_tile observation with
  | Model.Plant plant -> Some plant
  | Model.Empty | Model.Locked | Model.Weed | Model.Structure _ | Model.Animal _ -> None
;;

let step (observation : Model.observation) _ = Vint observation.obs_step
let day (observation : Model.observation) _ = Vint observation.obs_day
let hour (observation : Model.observation) _ = Vint observation.obs_hour

let money observation _ =
  let money = (own_farm observation).money in
  if Float.is_integer money
  then Vint (int_of_float money)
  else
    error
      "money is %g, which is not an integer; the DSL has no float arithmetic and money \
       was expected to be integral"
      money
;;

let seeds (observation : Model.observation) parameters =
  Vint observation.obs_private.seeds.(crop_index parameters)
;;

let shed_units (observation : Model.observation) parameters =
  Vint observation.obs_private.shed.(crop_index parameters)
;;

let market_price (observation : Model.observation) parameters =
  Vint observation.obs_market_prices.(crop_index parameters)
;;

let seed_cost _ parameters = Vint Model.seed_costs.(crop_index parameters)

let carried_units (observation : Model.observation) _ =
  let inventories = observation.obs_private.inventories in
  let total =
    if Array.length inventories = 0
    then 0
    else Array.fold_left (fun total count -> total + max 0 count) 0 inventories.(0).counts
  in
  Vint total
;;

let on_shed_access observation _ =
  let farm = own_farm observation in
  Vbool
    (Model.is_shed_adjacent
       ~board_size:observation.obs_board_size
       farm.farmer_x
       farm.farmer_y)
;;

let tile_is_empty observation _ =
  Vbool
    (match current_tile observation with
     | Model.Empty -> true
     | Model.Locked | Model.Weed | Model.Plant _ | Model.Structure _ | Model.Animal _ ->
       false)
;;

let tile_is_plant observation _ = Vbool (plant_tile observation <> None)

let tile_planted_day observation _ =
  match plant_tile observation with
  | None -> Vint (-1)
  | Some plant -> Vint plant.planted_day
;;

let tile_yield_units observation _ =
  match plant_tile observation with
  | None -> Vint 0
  | Some plant -> Vint plant.yield_units
;;

let tile_watered_today observation _ =
  match plant_tile observation with
  | None -> Vbool false
  | Some plant -> Vbool plant.watered_today
;;

let kinds = Vocabulary.kinds

let accessors =
  [ "step", step
  ; "day", day
  ; "hour", hour
  ; "money", money
  ; "seeds", seeds
  ; "shed_units", shed_units
  ; "market_price", market_price
  ; "seed_cost", seed_cost
  ; "carried_units", carried_units
  ; "on_shed_access", on_shed_access
  ; "tile_is_empty", tile_is_empty
  ; "tile_is_plant", tile_is_plant
  ; "tile_planted_day", tile_planted_day
  ; "tile_yield_units", tile_yield_units
  ; "tile_watered_today", tile_watered_today
  ]
;;

let t : Model.observation Policy_dsl.Interpreter.vocabulary =
  Policy_dsl.Interpreter.vocabulary ~kinds ~accessors ~requires:Vocabulary.requires ()
;;
