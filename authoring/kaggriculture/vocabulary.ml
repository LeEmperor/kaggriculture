(* The Kaggriculture observation vocabulary, as typed handles.

   One entry per accessor in submission/vocabulary.py's KINDS table, with the same names
   and the same kinds — that file is the implementation this table must agree with, and
   the Python loader rejects any family referencing a name that is not in its copy. Money
   is an int there for a reason documented in that file; here that decision is simply
   inherited by the handle's kind. *)

open Policy_family.Expr

let step = Obs.int "step"
let day = Obs.int "day"
let hour = Obs.int "hour"
let money = Obs.int "money"
let seeds = Obs.int "seeds"
let shed_units = Obs.int "shed_units"
let market_price = Obs.int "market_price"
let seed_cost = Obs.int "seed_cost"
let carried_units = Obs.int "carried_units"
let on_shed_access = Obs.bool "on_shed_access"
let tile_is_empty = Obs.bool "tile_is_empty"
let tile_is_plant = Obs.bool "tile_is_plant"
let tile_planted_day = Obs.int "tile_planted_day"
let tile_yield_units = Obs.int "tile_yield_units"
let tile_watered_today = Obs.bool "tile_watered_today"

(* Which parameters each accessor reads — the mirror of REQUIRES in
   submission/vocabulary.py. Four accessors resolve their item through the candidate's
   [crop], so a family reading one of them must declare a parameter by that name; the
   others read none and are absent here.

   This table is declarative on the OCaml side. Policy_family is game-agnostic and so
   cannot consume it, which leaves Interpreter.__init__ on the Python side as the
   enforcing gate — the same division of labour as the emit signatures in actions.ml. Keep
   the two lists identical. *)
let requires =
  [ "seeds", [ "crop" ]
  ; "shed_units", [ "crop" ]
  ; "market_price", [ "crop" ]
  ; "seed_cost", [ "crop" ]
  ]
;;
