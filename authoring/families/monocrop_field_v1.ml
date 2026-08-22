(* monocrop_field_v1: one tile, farmed on a full wheat cycle.

   The name says "field", but the DSL's game seam cannot reach one: every [tile_*]
   accessor reads the tile underfoot, and there is no MOVE emit, so this family works
   the single tile it spawns on. Widening that is Level A + Level B in
   docs/dsl_seam_extension.md, not something a rule can do. Until then "field" names
   the intent, and this file is the best a one-tile policy can do.

   What it fixes relative to the template it was copied from: the template buys one
   seed, plants it, and stops, so the plant is never watered and upstream turns it into
   a WEED at the end of its planting day. That costs exactly the seed price and nothing
   else ever happens. A wheat cycle needs WATER, HARVEST and DROP as well, and all
   three were already in Kaggriculture.Actions, unused.

   The cycle this encodes, for WHEAT (seed 10, first_yield_day 2, max_yield_day 4,
   max_yield 6, ongoing false):

     day d+0  PLANT, then WATER every day or the plant weeds out
              (consecutive_unwatered starts at 1 and kills at 2)
     day d+2  watering starts adding yield: window_start = (max_yield_day + 1) / 2
     day d+4  yield has reached 1 + 3 = 4; HARVEST, DROP at the shed, replant

   Harvesting later is strictly better than harvesting early: every harvest_age yields
   one unit per elapsed day, so the age only trades seed cost against nothing. 4 is the
   last age that still pays a watering bonus, and _decay_plants starts eating yield at
   step (planted_day + max_yield_day + 1) * turns_per_day, i.e. the morning of day d+5.
   So 4 is both the optimum and the deadline. It stays a parameter because that
   reasoning is wheat-specific. *)

open Policy_family
open Expr.O
module V = Kaggriculture.Vocabulary
module A = Kaggriculture.Actions

(* Four accessors -- seeds, shed_units, market_price, seed_cost -- resolve their item
   through this parameter, so it must be declared with exactly this name. *)
let (crop : Expr.skind Expr.param) = Expr.Param.enum "crop" ~values:[ "WHEAT" ]

(* Age in days at which a mature plant is harvested. Bounded by first_yield_day below
   and by the lifespan deadline above; HARVEST no-ops outside that window rather than
   failing, so the bounds are the useful search range, not a safety guard. *)
let (harvest_age : Expr.ikind Expr.param) = Expr.Param.int "harvest_age" ~min:2 ~max:4

(* Price floor for selling the shed out. Wheat quotes ran 26..47 on seed 1234, so this
   is a real knob and not a formality -- and it is the knob with a cliff in it.

   Measured on five ad-hoc seeds (1234/7/99/2024/31337), margin by floor:

     30   947  895  875  887  887
     38  1009  994  964  994  986
     42  1081 1072  -90 1072 1068

   42 is better on four seeds and catastrophic on the fifth: sell_stock is the only
   rule that converts shed units into money, and a floor the quote never reaches that
   episode means it never fires, so every seed bought is a dead loss. The failure is
   structural, not a bad value -- the rule has a floor but no deadline. Any search over
   this parameter will be pulled toward the cliff edge, so give the family an
   unconditional late-episode liquidation rule before searching it.

   38 is the default because it dominates 30 on every seed measured and stays clear of
   the cliff. Five ad-hoc seeds are not the validation split, so per
   docs/evaluation_protocol.md this is a bring-up starting point and not a promotion. *)
let (sell_floor : Expr.ikind Expr.param) = Expr.Param.int "sell_floor" ~min:0 ~max:200

let (last_step : Expr.ikind Expr.reg) =
  Expr.Reg.int "last_step" ~init:(-1) ~cls:Expr.Decision
;;

let turns_seen : Expr.ikind Expr.reg =
  Expr.Reg.int "turns_seen" ~init:0 ~cls:Expr.Telemetry
;;

(* Telemetry only: no guard reads it, so backends may diverge on it harmlessly. It is
   here because "how many cycles completed" is the one number that says whether this
   family is running or wedged, and policy_report prints it. *)
let harvests : Expr.ikind Expr.reg =
  Expr.Reg.int "harvests" ~init:0 ~cls:Expr.Telemetry
;;

let w = Family.write

(* Age of the plant underfoot, in days. tile_planted_day is -1 when the tile is not a
   plant, so this is only meaningful under a tile_is_plant guard. *)
let plant_age : Expr.ikind Expr.t = obs V.day -: obs V.tile_planted_day

let family : Family.t =
  Family.create
    ~policy_id:"monocrop_field_v1"
    ~family:"monocrop"
    ~family_version:1
    ~parameters:[ P crop; P harvest_age; P sell_floor ]
    ~registers:[ R last_step; R turns_seen; R harvests ]
    ~reset_when:(and_ [ obs V.step ==: int 0; state last_step >=: int 0 ])
    ~observe:[]
      (* select_all: these are independent orders, not alternatives.

         Both resolve AFTER the farmer action -- interpreter() applies the unit action
         and only then calls _process_market -- so a seed bought on turn t is first
         plantable on turn t+1. restock_seed therefore fires as soon as the shed of
         seeds empties, one cycle ahead of when the seed is needed, rather than trying
         to buy and plant in the same turn. *)
    ~market_rules:
      [ Family.rule
          ~name:"restock_seed"
          ~when_:(and_ [ obs V.seeds ==: int 0; obs V.money >=: obs V.seed_cost ])
          ~emit:(A.buy_seed ~crop:(param crop) ~units:(int 1))
      ; Family.rule
          ~name:"sell_stock"
          ~when_:
            (and_
               [ obs V.shed_units >: int 0
               ; obs V.market_price >=: param sell_floor
               ])
          ~emit:(A.sell ~crop:(param crop) ~units:(obs V.shed_units))
      ]
      (* select_first: list order IS priority order, and here it is load-bearing rather
         than incidental.

         water_plant sits above harvest_ready on purpose. On the harvest day the plant
         is still unwatered, and watering at age <= max_yield_day is worth one more
         unit, so watering first and harvesting on the next turn beats harvesting
         immediately. There are turnsPerDay = 24 turns in a day and this cycle needs at
         most four of them, so nothing is lost by spending turns this way.

         drop_to_shed sits above both because carried units are worth nothing until
         they reach the shed -- SELL sells out of the shed, not out of the worker's
         hands. The spawn tile (4,4) on a 10x10 board is one of the four shed-access
         tiles and is in the unlocked NW quadrant, which is the only reason DROP is
         reachable at all without a MOVE. *)
    ~farmer_cascade:
      [ Family.rule
          ~name:"drop_to_shed"
          ~when_:(and_ [ obs V.carried_units >: int 0; obs V.on_shed_access ])
          ~emit:A.drop
      ; Family.rule
          ~name:"water_plant"
          ~when_:(and_ [ obs V.tile_is_plant; not_ (obs V.tile_watered_today) ])
          ~emit:A.water
      ; Family.rule
          ~name:"harvest_ready"
          ~when_:
            (and_
               [ obs V.tile_is_plant
               ; obs V.tile_yield_units >: int 0
               ; plant_age >=: param harvest_age
               ])
          ~emit:A.harvest
      ; Family.rule
          ~name:"plant_seed"
          ~when_:(and_ [ obs V.tile_is_empty; obs V.seeds >: int 0 ])
          ~emit:(A.plant ~crop:(param crop))
      ; Family.rule ~name:"idle" ~when_:(bool true) ~emit:A.pass
      ]
    ~commit:
      [ w last_step (obs V.step)
      ; w turns_seen (state turns_seen +: int 1)
      ; w
          harvests
          (state harvests
           +: if_ (fired_any Expr.Farmer "harvest_ready") (int 1) (int 0))
      ]
;;
