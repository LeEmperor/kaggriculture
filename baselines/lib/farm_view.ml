(* Read-only helpers over [Model.observation], shared by every baseline.

   Everything here reads either the acting player's own farm and private state, or the
   market/town figures that are part of the shared observation. [Model.observation]
   also exposes the opponent's [farm] record, which upstream does put in the shared
   observation, but no baseline reads it: keeping the whole population inside "own farm
   plus public market" means a baseline can be transcribed into a submission-shaped
   policy later without re-auditing what it looked at. *)

module Model = Kag_model.Model

type t = Model.observation

let board_size (obs : t) = obs.obs_board_size
let me (obs : t) = obs.obs_farms.(obs.obs_player)
let priv (obs : t) = obs.obs_private
let day (obs : t) = obs.obs_day
let hour (obs : t) = obs.obs_hour

(* Integral by construction upstream (float startingMoney plus integer deltas); the
   baselines compare against integer thresholds, so round rather than carry a float. *)
let money (obs : t) = int_of_float (me obs).money
let index_of (obs : t) ~x ~y = (y * board_size obs) + x
let tile (obs : t) ~x ~y = (me obs).tiles.(index_of obs ~x ~y)
let coords (obs : t) index = index mod board_size obs, index / board_size obs

let spawn (obs : t) =
  let half = board_size obs / 2 in
  half - 1, half - 1
;;

let unit_count (obs : t) = 1 + Array.length (me obs).hands

let unit_pos (obs : t) ~unit =
  let farm = me obs in
  if unit = 0 then farm.farmer_x, farm.farmer_y else farm.hands.(unit - 1)
;;

let unit_inventory (obs : t) ~unit = (priv obs).inventories.(unit)
let carrying (obs : t) ~unit ~item = (unit_inventory obs ~unit).counts.(item)

let carried_total (obs : t) ~unit =
  Array.fold_left ( + ) 0 (unit_inventory obs ~unit).counts
;;

let shed (obs : t) ~item = (priv obs).shed.(item)
let shed_total (obs : t) = Array.fold_left ( + ) 0 (priv obs).shed
(* Shed capacity is configuration, not observation: [Model.observation] deliberately
   carries no config, and a real agent receives one from the framework alongside its
   observation. Baselines take the config at construction and pass it back in here. *)
let shed_room (obs : t) ~capacity = max 0 (capacity - shed_total obs)
let seeds (obs : t) ~crop = (priv obs).seeds.(crop)
let price (obs : t) ~item = obs.obs_market_prices.(item)
let market_inventory (obs : t) ~item = obs.obs_market_inventory.(item)
let on_shed_access (obs : t) ~unit =
  let x, y = unit_pos obs ~unit in
  Model.is_shed_adjacent ~board_size:(board_size obs) x y
;;

let manhattan (ax, ay) (bx, by) = abs (ax - bx) + abs (ay - by)

(* One step of a deterministic shortest path: close the x gap first, then the y gap.
   Any Manhattan-monotone rule costs the same number of turns; fixing the order keeps a
   baseline's trace reproducible. *)
let step_toward ~from:(x, y) ~target:(tx, ty) : Model.unit_op option =
  if x < tx
  then Some (Model.Move Model.East)
  else if x > tx
  then Some (Model.Move Model.West)
  else if y < ty
  then Some (Model.Move Model.South)
  else if y > ty
  then Some (Model.Move Model.North)
  else None
;;

(* Every tile the player owns, row-major. Ownership is exactly "not Locked": BUY_LAND
   turns a quadrant's Locked tiles into Empty, and nothing turns a tile back. *)
let iter_owned (obs : t) f =
  Array.iteri
    (fun index tile ->
      if tile <> Model.Locked
      then (
        let x, y = coords obs index in
        f ~x ~y tile))
    (me obs).tiles
;;

(* The owned tile minimizing (score, distance from [from], row-major index) among those
   [score] accepts. The three-way tie-break is what makes a tour deterministic: two
   equally good, equally distant tiles must always resolve the same way. *)
let best_owned (obs : t) ~from ~score =
  let best = ref None in
  iter_owned obs (fun ~x ~y tile ->
    match score ~x ~y tile with
    | None -> ()
    | Some rank ->
      let key = rank, manhattan from (x, y), index_of obs ~x ~y in
      (match !best with
       | Some (best_key, _) when best_key <= key -> ()
       | _ -> best := Some (key, (x, y))));
  match !best with
  | None -> None
  | Some (_, position) -> Some position
;;

let count_owned (obs : t) ~pred =
  let total = ref 0 in
  iter_owned obs (fun ~x ~y tile -> if pred ~x ~y tile then incr total);
  !total
;;

(* Turns left in the episode, and in today. Both baselines' liquidation rules and the
   "is there time to start another crop" question are phrased in these. *)
let turns_remaining (obs : t) ~episode_steps = max 0 (episode_steps - 1 - obs.obs_step)
let turns_left_today (obs : t) ~turns_per_day = turns_per_day - obs.obs_hour
