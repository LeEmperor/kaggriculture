(* The baseline opponent population, with its settings and its coverage declarations.

   Every entry declares the action shapes it exists to produce. That declaration is not
   documentation: `kag_sim evaluate --coverage` fails a run in which a declared shape
   never appeared, which is how "the baselines are non-vacuous" becomes a checked fact
   rather than a claim. Adding a baseline means adding its declaration here.

   Settings are deliberately round numbers chosen from the rule constants rather than
   tuned. These are measuring sticks; tuning them would make the league table a report
   about the tuning. *)

module Model = Kag_model.Model

type t =
  { id : string
  ; summary : string
  ; expect_ops : Coverage.op_tag list
  ; expect_orders : Coverage.order_tag list
  ; create : Model.config -> seat:int -> Model.policy
  }

let wheat = 0
let melon = 4
let cow = 1

(* Endgame thresholds shared by the population so that no baseline wins on liquidation
   timing alone. 48 turns is the last two days of selling; 12 turns is late enough that
   day 29's harvests still happen and early enough that a dropped load gets sold. *)
let sell_out_turns = 48
let dump_turns = 24

let pass =
  { id = "pass"
  ; summary = "does nothing; the floor every strategy must beat"
  ; expect_ops = [ Coverage.Pass ]
  ; expect_orders = []
  ; create = (fun _config ~seat:_ _obs -> Model.pass_action)
  }
;;

let random_valid =
  { id = "random-valid"
  ; summary = "uniform over the applicable action menu, seeded from the game seed"
    (* HARVEST is deliberately absent. Uniform play does reach it — four times in
       sixteen full episodes — but only by accidentally watering the same tile on two
       consecutive days and then standing on it while ripe, so declaring it would make
       the gate flaky. The finding is worth more than the assertion: the action space is
       not uniformly reachable, and a random agent cannot discover farming. *)
  ; expect_ops =
      [ Coverage.Move
      ; Coverage.Plant
      ; Coverage.Water
      ; Coverage.Dig
      ; Coverage.Build_coop
      ; Coverage.Build_pasture
      ; Coverage.Pickup
      ; Coverage.Place
      ; Coverage.Drop
      ]
  ; expect_orders =
      [ Coverage.Buy_seed
      ; Coverage.Sell
      ; Coverage.Hire
      ; Coverage.Buy_product
      ; Coverage.Buy_land
      ; Coverage.Buy_animal
      ]
  ; create =
      Random_valid.create
        { Random_valid.market_numerator = 1; market_denominator = 4; max_orders = 3 }
  }
;;

(* The endgame DROP is declared only where the crop cycle actually leaves a unit
   carrying a harvest on the last day. A twelve-day melon finishes its last cycle around
   day 25 and a market-focused plot is small enough to clear, so for those two the dump
   phase correctly does nothing and declaring DROP would assert a coincidence. *)
let field ?(drops = true) id summary spec =
  { id
  ; summary
  ; expect_ops =
      [ Coverage.Move; Coverage.Plant; Coverage.Water; Coverage.Harvest ]
      @ if drops then [ Coverage.Drop ] else []
  ; expect_orders = [ Coverage.Buy_seed; Coverage.Sell ]
  ; create = Field_policy.create spec
  }
;;

(* WHEAT: first yield day 2, max yield day 4, six units held at most, $10 a seed. Eight
   tiles is about what one walker can water in a 24-hour day from the shed corner. *)
let crop_greedy =
  field
    "crop-greedy"
    "short-cycle wheat on a small plot, sold on sight"
    { Field_policy.crop = wheat
    ; max_tiles = 8
    ; harvest_age = 4
    ; seed_target = 8
    ; reserve = 200
    ; sell_batch = 8
    ; sell_floor = 1
    ; hands_target = 0
    ; buy_land = false
    ; sell_out_turns
    ; dump_turns
    }
;;

(* MELON: nothing for ten days, then six units at a base price of 250. The same plot and
   the same single worker as crop-greedy, so the pair isolates cycle length. *)
let premium_crop =
  field
    ~drops:false
    "premium-crop"
    "long-cycle melon on the same plot; delayed return"
    { Field_policy.crop = melon
    ; max_tiles = 8
    ; harvest_age = 12
    ; seed_target = 8
    ; reserve = 100
    ; sell_batch = 4
    ; sell_floor = 1
    ; hands_target = 0
    ; buy_land = false
    ; sell_out_turns
    ; dump_turns
    }
;;

(* Hiring is close to free — the cost is the Fibonacci sequence in the day's hire count,
   so eight hands cost 1+1+2+3+5+8+13+21 = 54 a day — and land is the other way to buy
   more tiles. This baseline exists to find out how much that is worth. *)
let expansion =
  { (field
       "expansion"
       "re-hires a full crew daily and buys land; wheat across the whole farm"
       { Field_policy.crop = wheat
       ; max_tiles = 10 (* per unlocked quadrant *)
       ; harvest_age = 4
       ; seed_target = 20
       ; reserve = 1200
       ; sell_batch = 10
       ; sell_floor = 1
       ; hands_target = 8
       ; buy_land = true
       ; sell_out_turns
       ; dump_turns
       })
    with
    expect_orders = [ Coverage.Buy_seed; Coverage.Sell; Coverage.Hire; Coverage.Buy_land ]
  }
;;

let animal_focused =
  { id = "animal-focused"
  ; summary = "cows on pasture, fed from a hand-run wheat plot with market top-ups"
  ; expect_ops =
      [ Coverage.Move
      ; Coverage.Build_pasture
      ; Coverage.Pickup
      ; Coverage.Place
      ; Coverage.Feed
      ; Coverage.Care
      ; Coverage.Harvest
      ; Coverage.Collect_fertilizer
      ; Coverage.Water
      ; Coverage.Plant
      ]
  ; expect_orders =
      [ Coverage.Hire; Coverage.Buy_animal; Coverage.Buy_seed; Coverage.Sell ]
  ; create =
      Animal_focused.create
        { Animal_focused.animal = cow
        ; animal_target = 3
        ; feed_plot_tiles = 6
        ; hands_target = 3
        ; reserve = 200
        ; feed_stock = 20
        ; feed_price_cap = 40
        ; sell_batch = 3
        ; sell_out_turns
        ; dump_turns
        }
  }
;;

let market_focused =
  { id = "market-focused"
  ; summary = "holds wheat bought into cheap supply and releases it into scarcity"
  ; expect_ops = [ Coverage.Move; Coverage.Plant; Coverage.Water; Coverage.Harvest ]
  ; expect_orders =
      [ Coverage.Hire; Coverage.Buy_seed; Coverage.Buy_product; Coverage.Sell ]
  ; create =
      Market_focused.create
        { Market_focused.crop = wheat
        ; plot_tiles = 12
        ; hands_target = 3
        ; reserve = 300
        ; buy_below = 26
        ; sell_above = 32
        ; bundle = 20
        ; sell_out_turns
        ; dump_turns
        }
  }
;;

let all =
  [ pass
  ; random_valid
  ; crop_greedy
  ; premium_crop
  ; animal_focused
  ; expansion
  ; market_focused
  ]
;;

let find id = List.find_opt (fun baseline -> baseline.id = id) all
let ids = List.map (fun baseline -> baseline.id) all
