(* The Kaggriculture action vocabulary: typed emit constructors.

   These carry the per-game emit signatures — the same arities and operand kinds as
   WORKER_EMITS and MARKET_EMITS in submission/actions.py — so that a family author cannot
   build a malformed emit at all. The generic Family.emit stays reachable, which is why
   the Python loader's emit checks remain the final gate. *)

open Policy_family

let pass = Family.emit "PASS" []
let drop = Family.emit "DROP" []
let harvest = Family.emit "HARVEST" []
let water = Family.emit "WATER" []
let plant ~(crop : Expr.skind Expr.t) = Family.emit "PLANT" [ Family.O crop ]

let sell ~(crop : Expr.skind Expr.t) ~(units : Expr.ikind Expr.t) =
  Family.emit "SELL" [ Family.O crop; Family.O units ]
;;

let buy_seed ~(crop : Expr.skind Expr.t) ~(units : Expr.ikind Expr.t) =
  Family.emit "BUY_SEED" [ Family.O crop; Family.O units ]
;;
