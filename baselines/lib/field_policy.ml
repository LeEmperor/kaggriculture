(* Three of the six baselines are the same field operation run at different settings:
   crop-greedy is a short-cycle crop on a small plot, premium-crop is a long-cycle crop
   with the same worker, and expansion is a short-cycle crop with hired hands and bought
   land. Encoding them as one engine plus three [spec]s is deliberate — it makes the
   comparison between them a comparison of settings rather than of code, and it is the
   shape the "randomized variants of each baseline" the game plan asks for will need.

   The engine owns only the turn loop and the market queue; the field work itself is
   [Tending] and the endgame is [Trading]. *)

module Model = Kag_model.Model

type spec =
  { crop : int
  ; max_tiles : int (* per unlocked quadrant when [buy_land], else absolute *)
  ; harvest_age : int
  ; seed_target : int (* seeds kept on hand *)
  ; reserve : int (* money never spent on seeds *)
  ; sell_batch : int (* shed units before a routine sale *)
  ; sell_floor : int (* price below which a routine sale waits *)
  ; hands_target : int (* hands re-hired at the start of each day *)
  ; buy_land : bool
  ; sell_out_turns : int (* market stops holding back and dumps the shed *)
  ; dump_turns : int (* units start walking carried goods to the shed *)
  }

let tile_budget obs spec =
  if spec.buy_land
  then spec.max_tiles * (Farm_view.me obs).unlocked_quadrants
  else spec.max_tiles
;;

(* Hands evaporate at end of day and hires_today resets with them, so the whole hiring
   decision is "how many am I short, right now". Hiring early in the day is what makes
   them worth their cost: a hand hired at hour 23 does one thing. *)
let hire_orders obs spec ~max_orders =
  let have = Array.length (Farm_view.me obs).hands in
  let short = spec.hands_target - have in
  if short <= 0 then [] else List.init (min short max_orders) (fun _ -> Model.Hire)
;;

let land_order obs spec =
  if not spec.buy_land
  then None
  else (
    let extra = (Farm_view.me obs).unlocked_quadrants - 1 in
    if extra >= Array.length Model.land_prices
    then None
    else if Farm_view.money obs >= Model.land_prices.(extra) + spec.reserve
    then Some Model.Buy_land
    else None)
;;

let market obs spec ~(config : Model.config) =
  let max_orders = config.max_market_orders_per_turn in
  if Trading.within_final_turns obs ~config ~turns:spec.sell_out_turns
  then Trading.liquidation_orders obs ~config
  else (
    let orders =
      hire_orders obs spec ~max_orders
      @ List.filter_map Fun.id
          [ land_order obs spec
          ; Trading.restock_seeds
              obs
              ~crop:spec.crop
              ~target:(max spec.seed_target (tile_budget obs spec))
              ~reserve:spec.reserve
          ; Trading.sell_holding
              obs
              ~item:spec.crop
              ~min_units:spec.sell_batch
              ~min_price:spec.sell_floor
          ]
    in
    Array.of_list (List.filteri (fun index _ -> index < max_orders) orders))
;;

let create spec (config : Model.config) ~seat:_ : Model.policy =
  fun obs ->
  let plan =
    Tending.plan_for
      obs
      ~config
      ~crop:spec.crop
      ~max_tiles:(tile_budget obs spec)
      ~harvest_age:spec.harvest_age
  in
  let farmer, hands =
    if Trading.within_final_turns obs ~config ~turns:spec.dump_turns
    then (
      (* Field work continues underneath the dump: a unit with empty hands harvests, and
         drops what it harvested on a later turn. *)
      let assigned =
        Tending.assign_units
          obs
          ~plan
          ~units:(List.init (Farm_view.unit_count obs) Fun.id)
      in
      Trading.dump_ops obs ~fallback:(fun ~unit ->
        match List.assoc_opt unit assigned with
        | Some op -> op
        | None -> Model.Unit_pass))
    else Tending.assign obs ~plan
  in
  { Model.farmer; hands; market = market obs spec ~config }
;;
