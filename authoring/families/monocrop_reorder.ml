(*monocrop-reorder-v1 as a typed OCaml value.

   The authoring source for experiments/policies/monocrop_reorder/family.json:
   the emitter serializes this value, and the emitted JSON is what the Python
   interpreter loads and what the golden vectors prove correct. A change to the
   family belongs here first; the JSON is generated output. *)
(* *)
(* *)
(* *)
(* *)

open Policy_family
open Expr.O
module V = Kaggriculture.Vocabulary
module A = Kaggriculture.Actions

(* Parameters: the configuration registers, set by the candidate. *)
let crop = Expr.Param.enum "crop" ~values:["WHEAT"]

let cash_reserve = Expr.Param.int "cash_reserve" ~min:0

let seed_reorder_point = Expr.Param.int "seed_reorder_point" ~min:0

let seed_buy_batch = Expr.Param.int "seed_buy_batch" ~min:1

let planting_hour_cutoff = Expr.Param.int "planting_hour_cutoff" ~min:0 ~max:22

let harvest_min_age_days = Expr.Param.int "harvest_min_age_days" ~min:2 ~max:5

let sell_price_threshold = Expr.Param.int "sell_price_threshold" ~min:1

let liquidation_start_day =
  Expr.Param.int "liquidation_start_day" ~min:0 ~max:29

(* Registers: the per-episode state. Decision registers are the ones a guard
   reads; every backend must agree on them. Telemetry may diverge harmlessly. *)
let mode =
  Expr.Reg.enum "mode"
    ~values:["OPENING"; "PRODUCTION"; "LIQUIDATION"]
    ~init:"OPENING" ~cls:Expr.Decision

let last_step = Expr.Reg.int "last_step" ~init:(-1) ~cls:Expr.Decision

let requested_plant_actions =
  Expr.Reg.int "requested_plant_actions" ~init:0 ~cls:Expr.Decision

let mode_entered_step =
  Expr.Reg.int "mode_entered_step" ~init:0 ~cls:Expr.Telemetry

let money_seen = Expr.Reg.bool "money_seen" ~init:false ~cls:Expr.Telemetry

let previous_money = Expr.Reg.int "previous_money" ~init:0 ~cls:Expr.Telemetry

let last_money_delta =
  Expr.Reg.int "last_money_delta" ~init:0 ~cls:Expr.Telemetry

let peak_price = Expr.Reg.int "peak_price" ~init:0 ~cls:Expr.Telemetry

let requested_harvest_actions =
  Expr.Reg.int "requested_harvest_actions" ~init:0 ~cls:Expr.Telemetry

let requested_sell_units =
  Expr.Reg.int "requested_sell_units" ~init:0 ~cls:Expr.Telemetry

let w = Family.write

let family =
  Family.create ~policy_id:"monocrop-reorder-v1" ~family:"monocrop_reorder"
    ~family_version:1
    ~parameters:
      [ P crop
      ; P cash_reserve
      ; P seed_reorder_point
      ; P seed_buy_batch
      ; P planting_hour_cutoff
      ; P harvest_min_age_days
      ; P sell_price_threshold
      ; P liquidation_start_day ]
    ~registers:
      [ R mode
      ; R last_step
      ; R requested_plant_actions
      ; R mode_entered_step
      ; R money_seen
      ; R previous_money
      ; R last_money_delta
      ; R peak_price
      ; R requested_harvest_actions
      ; R requested_sell_units ]
    ~reset_when:(and_ [obs V.step ==: int 0; state last_step >=: int 0])
    ~observe:
      [ w last_money_delta
          (if_ (state money_seen) (obs V.money -: state previous_money) (int 0))
      ; w previous_money (obs V.money)
      ; w money_seen (bool true)
      ; w peak_price (max_ (state peak_price) (obs V.market_price))
      ; w mode
          (if_
             (obs V.day >=: param liquidation_start_day)
             (str "LIQUIDATION")
             (if_
                (state requested_plant_actions >: int 0)
                (str "PRODUCTION") (state mode) ) )
      ; w mode_entered_step
          (if_
             (next mode <>: state mode)
             (obs V.step) (state mode_entered_step) ) ]
    ~market_rules:
      [ Family.rule ~name:"sell_stock"
          ~when_:
            (and_
               [ obs V.shed_units >: int 0
               ; or_
                   [ state mode ==: str "LIQUIDATION"
                   ; obs V.market_price >=: param sell_price_threshold ] ] )
          ~emit:(A.sell ~crop:(param crop) ~units:(obs V.shed_units))
      ; Family.rule ~name:"reorder_seeds"
          ~when_:
            (and_
               [ state mode <>: str "LIQUIDATION"
               ; obs V.seeds <=: param seed_reorder_point
               ; obs V.money -: (param seed_buy_batch *: obs V.seed_cost)
                 >=: param cash_reserve ] )
          ~emit:(A.buy_seed ~crop:(param crop) ~units:(param seed_buy_batch)) ]
    ~farmer_cascade:
      [ Family.rule ~name:"stow_carried"
          ~when_:(and_ [obs V.carried_units >: int 0; obs V.on_shed_access])
          ~emit:A.drop
      ; Family.rule ~name:"harvest_ready"
          ~when_:
            (and_
               [ obs V.tile_is_plant
               ; obs V.day -: obs V.tile_planted_day
                 >=: param harvest_min_age_days
               ; obs V.tile_yield_units >: int 0 ] )
          ~emit:A.harvest
      ; Family.rule ~name:"water_crop"
          ~when_:(and_ [obs V.tile_is_plant; not_ (obs V.tile_watered_today)])
          ~emit:A.water
      ; Family.rule ~name:"tend_wait" ~when_:(obs V.tile_is_plant) ~emit:A.pass
      ; Family.rule ~name:"plant_seed"
          ~when_:
            (and_
               [ obs V.tile_is_empty
               ; obs V.seeds >: int 0
               ; obs V.hour <=: param planting_hour_cutoff
               ; state mode <>: str "LIQUIDATION" ] )
          ~emit:(A.plant ~crop:(param crop))
      ; Family.rule ~name:"idle" ~when_:(bool true) ~emit:A.pass ]
    ~commit:
      [ w requested_plant_actions
          ( state requested_plant_actions
          +: if_ (fired Expr.Farmer ==: str "plant_seed") (int 1) (int 0) )
      ; w requested_harvest_actions
          ( state requested_harvest_actions
          +: if_ (fired Expr.Farmer ==: str "harvest_ready") (int 1) (int 0) )
      ; w requested_sell_units
          ( state requested_sell_units
          +: if_ (fired_any Expr.Market "sell_stock") (obs V.shed_units) (int 0)
          )
      ; w last_step (obs V.step) ]
