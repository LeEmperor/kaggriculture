(* Trade the market rather than the field: hold inventory, buy into cheap supply, and sell
   in bundles when scarcity has moved the price.

   The exploitable structure is that the town consumes from the same market inventory the
   players trade against — every shop instance every fourth transition, plus the town
   centre every day — so supply falls monotonically over an episode and prices drift up.
   A holder therefore has a real edge over a seller-on-sight, and this baseline exists to
   measure how much of one. It grows a small plot as well, because a pure trader has
   nothing to sell that it did not first buy. *)

module Model = Kag_model.Model

type spec =
  { crop : int (* grown, and also the traded product *)
  ; plot_tiles : int
  ; hands_target : int
  ; reserve : int
  ; buy_below : int (* accumulate while the price is at or under this *)
  ; sell_above : int (* release the whole holding at or over this *)
  ; bundle : int (* minimum units in a bundled sale *)
  ; sell_out_turns : int
  ; dump_turns : int
  }

(* Products other than the traded one arrive only from harvests; they are sold on sight
   above their base price, since this baseline has no thesis about them. *)
let incidental_sales obs spec =
  List.filter_map
    (fun item ->
      if item = spec.crop
      then None
      else
        Trading.sell_holding
          obs
          ~item
          ~min_units:1
          ~min_price:Model.market_base_prices.(item))
    (List.init Model.product_count Fun.id)
;;

let market obs spec ~(config : Model.config) =
  let max_orders = config.max_market_orders_per_turn in
  if Trading.within_final_turns obs ~config ~turns:spec.sell_out_turns
  then Trading.liquidation_orders obs ~config
  else (
    let price = Farm_view.price obs ~item:spec.crop in
    let holding = Farm_view.shed obs ~item:spec.crop in
    let room = Farm_view.shed_room obs ~capacity:config.shed_capacity in
    let have_hands = Array.length (Farm_view.me obs).hands in
    let hires = List.init (max 0 (spec.hands_target - have_hands)) (fun _ -> Model.Hire) in
    let accumulate =
      if price <= spec.buy_below && room > 0
      then (
        let affordable = (Farm_view.money obs - spec.reserve) / max 1 price in
        let count = min room (max 0 affordable) in
        if count > 0 then Some (Model.Buy_product { item = spec.crop; count }) else None)
      else None
    in
    let release =
      if price >= spec.sell_above && holding >= spec.bundle
      then Some (Model.Sell { item = spec.crop; count = holding })
      else None
    in
    let orders =
      hires
      @ List.filter_map
          Fun.id
          [ release
          ; accumulate
          ; Trading.restock_seeds
              obs
              ~crop:spec.crop
              ~target:spec.plot_tiles
              ~reserve:spec.reserve
          ]
      @ incidental_sales obs spec
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
      ~max_tiles:spec.plot_tiles
      ~harvest_age:4
  in
  let farmer, hands =
    if Trading.within_final_turns obs ~config ~turns:spec.dump_turns
    then (
      let assigned =
        Tending.assign_units obs ~plan ~units:(List.init (Farm_view.unit_count obs) Fun.id)
      in
      Trading.dump_ops obs ~fallback:(fun ~unit ->
        match List.assoc_opt unit assigned with
        | Some op -> op
        | None -> Model.Unit_pass))
    else Tending.assign obs ~plan
  in
  { Model.farmer; hands; market = market obs spec ~config }
;;
