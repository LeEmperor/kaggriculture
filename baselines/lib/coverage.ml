(* Action-shape coverage for the non-vacuity check.

   A baseline that compiles, runs, and loses is indistinguishable from a baseline that
   emits PASS 719 times — both produce a number. This is what separates them: every
   baseline declares the action shapes it exists to produce, an evaluation run tallies
   what it actually produced, and a shape the baseline claimed but never emitted fails
   the run. It is the same idea as tools/coverage.py's gate on the differential
   population, applied to policies instead of tapes. *)

module Model = Kag_model.Model

type op_tag =
  | Pass
  | Move
  | Drop
  | Pickup
  | Place
  | Plant
  | Water
  | Harvest
  | Fertilize
  | Dig
  | Build_coop
  | Build_pasture
  | Feed
  | Care
  | Collect_fertilizer

type order_tag =
  | Hire
  | Buy_seed
  | Buy_animal
  | Sell
  | Buy_product
  | Buy_land
  | Bad_order

let op_tags =
  [ Pass, "PASS"
  ; Move, "MOVE"
  ; Drop, "DROP"
  ; Pickup, "PICKUP"
  ; Place, "PLACE"
  ; Plant, "PLANT"
  ; Water, "WATER"
  ; Harvest, "HARVEST"
  ; Fertilize, "FERTILIZE"
  ; Dig, "DIG"
  ; Build_coop, "BUILD_COOP"
  ; Build_pasture, "BUILD_PASTURE"
  ; Feed, "FEED"
  ; Care, "CARE"
  ; Collect_fertilizer, "COLLECT_FERTILIZER"
  ]
;;

let order_tags =
  [ Hire, "HIRE"
  ; Buy_seed, "BUY_SEED"
  ; Buy_animal, "BUY_ANIMAL"
  ; Sell, "SELL"
  ; Buy_product, "BUY_PRODUCT"
  ; Buy_land, "BUY_LAND"
  ; Bad_order, "BAD_ORDER"
  ]
;;

let op_name tag = List.assoc tag op_tags
let order_name tag = List.assoc tag order_tags

let op_tag : Model.unit_op -> op_tag = function
  | Model.Unit_pass -> Pass
  | Model.Move _ -> Move
  | Model.Drop -> Drop
  | Model.Pickup _ -> Pickup
  | Model.Place _ -> Place
  | Model.Plant_crop _ -> Plant
  | Model.Water -> Water
  | Model.Harvest -> Harvest
  | Model.Fertilize -> Fertilize
  | Model.Dig -> Dig
  | Model.Build_coop -> Build_coop
  | Model.Build_pasture -> Build_pasture
  | Model.Feed -> Feed
  | Model.Care -> Care
  | Model.Collect_fertilizer -> Collect_fertilizer
;;

let order_tag : Model.market_order -> order_tag = function
  | Model.Hire -> Hire
  | Model.Buy_seed _ -> Buy_seed
  | Model.Buy_animal _ -> Buy_animal
  | Model.Sell _ -> Sell
  | Model.Buy_product _ -> Buy_product
  | Model.Buy_land -> Buy_land
  | Model.Bad_order -> Bad_order
;;

let op_index tag =
  let rec find index = function
    | [] -> assert false
    | (candidate, _) :: rest -> if candidate = tag then index else find (index + 1) rest
  in
  find 0 op_tags
;;

let order_index tag =
  let rec find index = function
    | [] -> assert false
    | (candidate, _) :: rest -> if candidate = tag then index else find (index + 1) rest
  in
  find 0 order_tags
;;

type t =
  { ops : int array (* per op_tag, counting every unit's action *)
  ; orders : int array (* per order_tag *)
  ; units : int array (* per order_tag: units requested, not orders issued *)
  ; mutable turns : int
  ; mutable hand_actions : int
  }

let create () =
  { ops = Array.make (List.length op_tags) 0
  ; orders = Array.make (List.length order_tags) 0
  ; units = Array.make (List.length order_tags) 0
  ; turns = 0
  ; hand_actions = 0
  }
;;

let order_units : Model.market_order -> int = function
  | Model.Buy_seed { count; _ }
  | Model.Buy_animal { count; _ }
  | Model.Sell { count; _ }
  | Model.Buy_product { count; _ } -> count
  | Model.Hire | Model.Buy_land | Model.Bad_order -> 1
;;

let observe t (action : Model.player_action) =
  t.turns <- t.turns + 1;
  let count_op op = t.ops.(op_index (op_tag op)) <- t.ops.(op_index (op_tag op)) + 1 in
  count_op action.farmer;
  Array.iter
    (fun op ->
      t.hand_actions <- t.hand_actions + 1;
      count_op op)
    action.hands;
  Array.iter
    (fun order ->
      let index = order_index (order_tag order) in
      t.orders.(index) <- t.orders.(index) + 1;
      t.units.(index) <- t.units.(index) + order_units order)
    action.market
;;

let merge into from =
  Array.iteri (fun i v -> into.ops.(i) <- into.ops.(i) + v) from.ops;
  Array.iteri (fun i v -> into.orders.(i) <- into.orders.(i) + v) from.orders;
  Array.iteri (fun i v -> into.units.(i) <- into.units.(i) + v) from.units;
  into.turns <- into.turns + from.turns;
  into.hand_actions <- into.hand_actions + from.hand_actions
;;

let op_count t tag = t.ops.(op_index tag)
let order_count t tag = t.orders.(order_index tag)
let order_unit_count t tag = t.units.(order_index tag)

(* The shapes a baseline claimed but never produced. An empty list is the pass
   condition; anything in it means the declaration and the behaviour disagree, and one
   of the two is wrong. *)
let missing t ~expect_ops ~expect_orders =
  List.filter_map
    (fun tag -> if op_count t tag = 0 then Some (op_name tag) else None)
    expect_ops
  @ List.filter_map
      (fun tag -> if order_count t tag = 0 then Some (order_name tag) else None)
      expect_orders
;;
