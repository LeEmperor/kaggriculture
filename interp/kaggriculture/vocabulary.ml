(* The Kaggriculture observation vocabulary over JSON observations: the interpreter's only
   game-aware seam on the reading side.

   A line-for-line port of submission/vocabulary.py. That file is the specification, not
   merely the prior art — the golden vectors were recorded through it, and any divergence
   here shows up as a failed vector rather than as a subtle drift, which is the whole point
   of adding an [ocaml] column to experiments/golden.py.

   docs/policy_dsl.md is blunt about where the cost of a data-defined family actually
   lives: not in the rules, which are free, but in the accessors. Adding opponent_money
   costs one line in every backend. Adding a guard that uses it costs nothing. So this file
   is the part that gets ported per backend, and policy_dsl/ is the part that does not.

   Accessors take (observation, parameters) because several of them resolve param.crop at
   evaluation time, which is what keeps the family crop-generic even while v1 pins the crop
   to WHEAT. *)

open Policy_dsl.Expr

exception Observation_error of string

let error fmt = Printf.ksprintf (fun message -> raise (Observation_error message)) fmt

(* Official fixed seed prices. Mirrors experiments/policies/common/game_data.py. *)
let seed_costs =
  [ "WHEAT", 10; "CARROT", 20; "TOMATO", 50; "STRAWBERRY", 100; "MELON", 80 ]
;;

(* ------------------------------------------------------------------ *)
(* JSON readers *)
(* ------------------------------------------------------------------ *)

let field key json =
  match json with
  | `Assoc fields -> List.assoc_opt key fields
  | _ -> None
;;

let require key json =
  match field key json with
  | Some value -> value
  | None -> error "observation is missing '%s'" key
;;

(* Python's int(): exact on an integer, truncating toward zero on a float. *)
let to_int name json =
  match json with
  | `Int n -> n
  | `Float f -> int_of_float (Float.trunc f)
  | other -> error "%s is %s, which is not a number" name (Yojson.Safe.to_string other)
;;

let to_bool json =
  match json with
  | `Bool b -> b
  | `Null -> false
  | `Int n -> n <> 0
  | other -> error "expected a boolean, got %s" (Yojson.Safe.to_string other)
;;

let nth_exn items index name =
  match List.nth_opt items index with
  | Some item -> item
  | None -> error "%s index %d is out of range" name index
;;

let items json name =
  match json with
  | `List items -> items
  | other -> error "%s is %s, not an array" name (Yojson.Safe.to_string other)
;;

(* money is declared float in the observation schema but is integral by construction
   upstream: it starts at float(startingMoney) and every delta is an integer price, cost,
   or hire fee. Exposing it to the DSL as an int is therefore exact, not an approximation,
   and it keeps floats out of a language that deliberately has no float arithmetic. If that
   ever stops being true this raises rather than silently diverging. *)
let integral name json =
  let number =
    match json with
    | `Int n -> float_of_int n
    | `Float f -> f
    | other -> error "%s is %s, which is not a number" name (Yojson.Safe.to_string other)
  in
  if Float.is_integer number
  then int_of_float number
  else
    error
      "%s is %g, which is not an integer; the DSL has no float arithmetic and money was \
       expected to be integral"
      name
      number
;;

(* ------------------------------------------------------------------ *)
(* Shared readers *)
(* ------------------------------------------------------------------ *)

let own_farm observation =
  let player = to_int "player" (require "player" observation) in
  nth_exn (items (require "farms" observation) "farms") player "farms"
;;

let item_count container item =
  match container with
  | None | Some `Null -> 0
  | Some container ->
    (match field item container with
     | None -> 0
     | Some value -> max 0 (to_int item value))
;;

let crop parameters =
  match SM.find_opt "crop" parameters with
  | Some (Vstr crop) -> crop
  | _ -> error "the family's 'crop' parameter is missing or not a string"
;;

let farmer_xy farm =
  match items (require "farmer" farm) "farmer" with
  | [ x; y ] -> to_int "farmer.x" x, to_int "farmer.y" y
  | _ -> error "farmer must be a two-element [x, y] array"
;;

let current_tile farm =
  let x, y = farmer_xy farm in
  let rows = items (require "tiles" farm) "tiles" in
  nth_exn (items (nth_exn rows y "tiles") "tiles row") x "tiles row"
;;

let plant_tile observation =
  let tile = current_tile (own_farm observation) in
  match field "kind" tile with
  | Some (`String "PLANT") -> Some tile
  | _ -> None
;;

let worker_inventory ?(worker = 0) private_state =
  let inventories =
    match field "inventories" private_state with
    | None | Some `Null -> []
    | Some value -> items value "inventories"
  in
  match List.nth_opt inventories worker with
  | Some (`Assoc fields) -> fields
  | _ -> []
;;

(* ------------------------------------------------------------------ *)
(* Accessors *)
(* ------------------------------------------------------------------ *)

let int_of observation key = Vint (to_int key (require key observation))
let step observation _ = int_of observation "step"
let day observation _ = int_of observation "day"
let hour observation _ = int_of observation "hour"
let money observation _ = Vint (integral "money" (require "money" (own_farm observation)))

let seeds observation parameters =
  Vint (item_count (field "seeds" (require "private" observation)) (crop parameters))
;;

let shed_units observation parameters =
  Vint (item_count (field "shed" (require "private" observation)) (crop parameters))
;;

let market_price observation parameters =
  Vint (item_count (field "prices" (require "market" observation)) (crop parameters))
;;

let seed_cost _ parameters =
  let crop = crop parameters in
  match List.assoc_opt crop seed_costs with
  | Some cost -> Vint cost
  | None -> error "unknown crop: %s" crop
;;

let carried_units observation _ =
  Vint
    (List.fold_left
       (fun total (item, count) -> total + max 0 (to_int item count))
       0
       (worker_inventory (require "private" observation)))
;;

let on_shed_access observation _ =
  let farm = own_farm observation in
  let rows = items (require "tiles" farm) "tiles" in
  let height = List.length rows in
  let width =
    match rows with
    | [] -> 0
    | row :: _ -> List.length (items row "tiles row")
  in
  if width < 2 || height < 2
  then Vbool false
  else (
    let x, y = farmer_xy farm in
    let middle_x = width / 2
    and middle_y = height / 2 in
    Vbool
      (List.mem
         (x, y)
         [ middle_x - 1, middle_y - 1
         ; middle_x, middle_y - 1
         ; middle_x - 1, middle_y
         ; middle_x, middle_y
         ]))
;;

let tile_is_empty observation _ = Vbool (current_tile (own_farm observation) = `Null)
let tile_is_plant observation _ = Vbool (plant_tile observation <> None)

let tile_planted_day observation _ =
  match plant_tile observation with
  | None -> Vint (-1)
  | Some tile -> Vint (to_int "planted_day" (require "planted_day" tile))
;;

let tile_yield_units observation _ =
  match plant_tile observation with
  | None -> Vint 0
  | Some tile ->
    Vint
      (match field "yield_units" tile with
       | None -> 0
       | Some value -> to_int "yield_units" value)
;;

let tile_watered_today observation _ =
  match plant_tile observation with
  | None -> Vbool false
  | Some tile ->
    Vbool
      (match field "watered_today" tile with
       | None -> false
       | Some value -> to_bool value)
;;

let kinds =
  [ "step", int_kind
  ; "day", int_kind
  ; "hour", int_kind
  ; "money", int_kind
  ; "seeds", int_kind
  ; "shed_units", int_kind
  ; "market_price", int_kind
  ; "seed_cost", int_kind
  ; "carried_units", int_kind
  ; "on_shed_access", bool_kind
  ; "tile_is_empty", bool_kind
  ; "tile_is_plant", bool_kind
  ; "tile_planted_day", int_kind
  ; "tile_yield_units", int_kind
  ; "tile_watered_today", bool_kind
  ]
;;

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

(* Which parameters each accessor reads — the mirror of REQUIRES in
   submission/vocabulary.py and of [requires] in authoring/kaggriculture/vocabulary.ml.
   Unlike the authoring copy this one is enforced: Interpreter.create consumes it. *)
let requires =
  [ "seeds", [ "crop" ]
  ; "shed_units", [ "crop" ]
  ; "market_price", [ "crop" ]
  ; "seed_cost", [ "crop" ]
  ]
;;

(* Annotated rather than inferred: the accessors only ever match the constructors they
   need, so without this the vocabulary generalizes to an open polymorphic variant and
   cannot be a module-level value. *)
let t : Yojson.Safe.t Policy_dsl.Interpreter.vocabulary =
  Policy_dsl.Interpreter.vocabulary ~kinds ~accessors ~requires ()
;;
