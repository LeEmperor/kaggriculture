open Policy_family
open Expr.O
module V = Kaggriculture.Vocabulary
module A = Kaggriculture.Actions

let (crop : Expr.skind Expr.param) = Expr.Param.enum "crop" ~values:[ "WHEAT" ]

let (last_step : Expr.ikind Expr.reg) =
  (Expr.Reg.int "last_step" ~init:(-1) ~cls:Expr.Decision : Expr.ikind Expr.reg)
;;

let turns_seen : Expr.ikind Expr.reg =
  Expr.Reg.int "turns_seen" ~init:0 ~cls:Expr.Telemetry
;;
let (seeds_bought : Expr.bkind Expr.reg) =
  Expr.Reg.bool "seeds_bought" ~init:false ~cls:Expr.Decision
;;
let w = Family.write

let funky_emit : Family.emit =
  Family.emit "SELL" [ Family.O (param crop); Family.O (obs V.shed_units) ]
;;

let funky_when : Expr.bkind Expr.t =
  and_ [ obs V.shed_units >: int 0; obs V.market_price >=: int 30 ]
;;

let funky_rule : Family.rule =
  Family.rule ~name:"mashallah_rule" ~when_:funky_when ~emit:funky_emit
;;

(* owning toplevel of the strategy - might rename to "strategy" later *)
let family : Family.t =
  (Family.create
     ~policy_id:"template_v1"
     ~family:"template"
     ~family_version:1
     ~parameters:[ P crop ]
     ~registers:[ R last_step; R turns_seen; R seeds_bought ]
     ~reset_when:(and_ [ obs V.step ==: int 0; state last_step >=: int 0 ])
     ~observe:[]
     ~market_rules:
       ([ Family.rule
            ~name:"stock_up"
            ~when_:(and_ [ not_ (state seeds_bought); obs V.money >=: int 500 ])
            ~emit:(A.buy_seed ~crop:(param crop) ~units:(int 1))
        ; funky_rule
        ]
        : Family.rule list)
     ~farmer_cascade:
       [ Family.rule
           ~name:"plant_it"
           ~when_:(and_ [ obs V.tile_is_empty; obs V.seeds >: int 0 ])
           ~emit:(A.plant ~crop:(param crop))
       ; Family.rule ~name:"idle" ~when_:(bool true) ~emit:A.pass
       ]
     ~commit:
       [ w last_step (obs V.step)
       ; w turns_seen (state turns_seen +: int 1)
       ; w seeds_bought (or_ [ state seeds_bought; fired_any Expr.Market "stock_up" ])
       ]
   : Family.t)
;;
