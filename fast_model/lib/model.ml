(* The scalar transition engine — currently the verified PASS/init/terminal
   slice, ported behaviour-for-behaviour from the retired C++ scaffold.

   Phase 3 of docs/kaggriculture_gameplan.md grows this file rule group by rule
   group; nothing here is trusted for research until the Phase 4 differential
   gate passes. The API shape is the game plan's: initial_state / step /
   observe / copy / run_game over flat mutable state, no strings or
   allocation inside [step]. *)

let board_size = 10
let tile_count = board_size * board_size
let player_count = 2

type tile =
  | Empty
  | Locked

type status =
  | Active
  | Done

type config =
  { episode_steps : int
  ; turns_per_day : int
  ; board_size : int
  ; starting_money : int
  ; seed : int
  }

let default_config =
  { episode_steps = 720; turns_per_day = 24; board_size = 10; starting_money = 3000; seed = 0 }

type farm =
  { mutable money : float
  ; mutable farmer_x : int
  ; mutable farmer_y : int
  ; tiles : tile array
  }

type state =
  { config : config
  ; farms : farm array
  ; mutable transitions : int
  ; mutable day : int
  ; mutable hour : int
  ; mutable status : status
  }

type player_action = Pass

type observation =
  { obs_player : int
  ; obs_transitions : int
  ; obs_day : int
  ; obs_hour : int
  ; obs_status : status
  }

let initial_state config =
  if config.board_size <> board_size
  then invalid_arg "the initial scalar slice currently supports only boardSize=10";
  if config.episode_steps < 2 then invalid_arg "episodeSteps must be at least 2";
  if config.turns_per_day <= 0 then invalid_arg "turnsPerDay must be positive";
  let farm () =
    let tiles =
      Array.init tile_count (fun index ->
        let x = index mod board_size
        and y = index / board_size in
        if x < 5 && y < 5 then Empty else Locked)
    in
    { money = float_of_int config.starting_money; farmer_x = 4; farmer_y = 4; tiles }
  in
  { config
  ; farms = Array.init player_count (fun _ -> farm ())
  ; transitions = 0
  ; day = 0
  ; hour = 0
  ; status = Active
  }

let copy state =
  { state with
    farms =
      Array.map
        (fun farm -> { farm with tiles = Array.copy farm.tiles })
        state.farms
  }

let step state (_ : player_action) (_ : player_action) =
  if state.status = Done then invalid_arg "cannot step a completed game";
  (* PASS is the only operation in this first verified rule slice. *)
  state.transitions <- state.transitions + 1;
  state.day <- state.transitions / state.config.turns_per_day;
  state.hour <- state.transitions mod state.config.turns_per_day;
  (* The interpreter sees prior observation steps 0..episodeSteps-2. It marks
     DONE while processing episodeSteps-2, producing episodeSteps-1 transitions. *)
  if state.transitions >= state.config.episode_steps - 1 then state.status <- Done

let observe state ~player =
  if player < 0 || player >= player_count then invalid_arg "player must be 0 or 1";
  { obs_player = player
  ; obs_transitions = state.transitions
  ; obs_day = state.day
  ; obs_hour = state.hour
  ; obs_status = state.status
  }

type result =
  { final_money : float array
  ; result_transitions : int
  }

(* The only policy the slice supports; a real policy type arrives with the
   first non-PASS rule group. *)
let run_game config =
  let state = initial_state config in
  while state.status = Active do
    step state Pass Pass
  done;
  { final_money = Array.map (fun farm -> farm.money) state.farms
  ; result_transitions = state.transitions
  }
