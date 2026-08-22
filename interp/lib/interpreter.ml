(* Binds the four DSL roles to an injected, game-specific vocabulary.

   This holds the turn loop and nothing else: the language is in Expr, the selection
   disciplines are in Cascade, the stage semantics are in Pipeline, and the artifact is in
   Family. Whatever game knowledge the running policy needs arrives through the two
   injected seams — a vocabulary and an action builder — so this file can be read without
   knowing what a crop is.

   The observation type is a parameter rather than a fixed JSON value. The Python original
   could leave it as Any; here making it ['obs] is what lets one interpreter serve both a
   JSON observation replayed from a golden vector and a native simulator observation read
   straight out of Kag_model, with no conversion layer between them.

   Observation accessors are resolved lazily and memoised for the duration of one turn.
   Laziness is load-bearing rather than an optimisation: [and] short-circuits, so a guard
   such as harvest_ready never asks for tile_planted_day on a tile that is not a plant. *)

open Expr

type 'obs accessor = 'obs -> value SM.t -> value

(* [kinds] is what Family.load type-checks against; [accessors] is what the turn loop
   calls. They are separate fields because the first is needed before any observation
   exists and the second only afterwards, and because keeping the declaration next to the
   implementation is what stops the two drifting apart.

   [requires] records the third thing a caller cannot otherwise see: which *parameters* an
   accessor reads. An accessor that resolves an item through parameters["crop"] is
   undefined for a family that declares no such parameter, and nothing in [kinds] says so.
   Accessors that read no parameter are simply absent. *)
type 'obs vocabulary =
  { kinds : kind SM.t
  ; accessors : 'obs accessor SM.t
  ; requires : string list SM.t
  }

let of_map pairs =
  List.fold_left (fun map (key, value) -> SM.add key value map) SM.empty pairs
;;

let names map = SS.of_list (List.map fst (SM.bindings map))

let vocabulary ?(requires = []) ~kinds ~accessors () =
  let kinds = of_map kinds
  and accessors = of_map accessors
  and requires = of_map requires in
  let undeclared = SS.diff (names accessors) (names kinds)
  and unimplemented = SS.diff (names kinds) (names accessors) in
  if not (SS.is_empty undeclared && SS.is_empty unimplemented)
  then
    failf
      "vocabulary"
      "declared-but-missing: %s; implemented-but-undeclared: %s"
      (match SS.elements unimplemented with
       | [] -> "<none>"
       | missing -> String.concat ", " missing)
      (match SS.elements undeclared with
       | [] -> "<none>"
       | stray -> String.concat ", " stray);
  let stray = SS.diff (names requires) (names kinds) in
  if not (SS.is_empty stray)
  then
    failf
      "vocabulary.requires"
      "names no accessor declares: %s"
      (String.concat ", " (SS.elements stray));
  { kinds; accessors; requires }
;;

(* What the decide stage produced, before it is shaped into a game action. *)
type turn =
  { farmer : Cascade.firing option
  ; market : Cascade.firing list
  }

(* A family plus bound parameters: pure, stateless, reusable across players. *)
type ('obs, 'action) t =
  { family : Family.t
  ; parameters : value SM.t
  ; vocabulary : 'obs vocabulary
  ; build_action : turn -> 'action
  }

let create ~family ~parameters ~vocabulary ~build_action =
  let declared = SS.of_list family.Family.parameter_order in
  let missing = SS.diff declared (names parameters) in
  if not (SS.is_empty missing)
  then
    failf
      "interpreter"
      "unbound parameters: %s"
      (String.concat ", " (SS.elements missing));
  (* Every accessor this family actually reads must have the parameters it depends on.
     Checked here because this is the first point at which the family, the vocabulary, and
     the bound parameters are all in hand. *)
  let undeclared =
    SS.fold
      (fun observation undeclared ->
        List.fold_left
          (fun undeclared name ->
            if SM.mem name parameters
            then undeclared
            else (
              let readers =
                try SM.find name undeclared with
                | Not_found -> []
              in
              SM.add name (readers @ [ observation ]) undeclared))
          undeclared
          (try SM.find observation vocabulary.requires with
           | Not_found -> []))
      (Family.observation_names family)
      SM.empty
  in
  if not (SM.is_empty undeclared)
  then
    failf
      "interpreter"
      "family declares no parameter %s"
      (String.concat
         "; "
         (List.map
            (fun (name, readers) ->
              Printf.sprintf "'%s' (needed by %s)" name (String.concat ", " readers))
            (SM.bindings undeclared)));
  { family; parameters; vocabulary; build_action }
;;

let initial_registers t = Pipeline.initial_registers (Family.register_list t.family)

(* The only runtime type check in the interpreter, and it belongs exactly here: everything
   inside has been checked statically by Family.load, while an accessor reads a structure
   the game handed us. *)
let resolver t observation =
  let cache = Hashtbl.create 16 in
  fun name ->
    match Hashtbl.find_opt cache name with
    | Some value -> value
    | None ->
      let accessor =
        match SM.find_opt name t.vocabulary.accessors with
        | Some accessor -> accessor
        | None -> failf "obs" "no accessor for '%s'" name
      in
      let value =
        check_value
          (SM.find name t.vocabulary.kinds)
          (accessor observation t.parameters)
          ("obs." ^ name)
      in
      Hashtbl.add cache name value;
      value
;;

(* One turn: (observation, registers) -> (action, next registers). *)
let step t observation registers =
  let observe = resolver t observation in
  let env state next groups =
    { e_params = t.parameters
    ; e_state = state
    ; e_observe = observe
    ; e_next = next
    ; e_groups = groups
    }
  in
  (* Stage 0 - reset. Restoring every init reproduces the hand-written policy's habit of
     constructing a fresh PolicyState. *)
  let state =
    match evaluate t.family.Family.reset_when (env registers SM.empty SM.empty) with
    | Vbool true -> initial_registers t
    | Vbool false -> registers
    | other -> failf "reset_when" "produced %s, not a boolean" (value_name other)
  in
  (* Stage 1 - observe. Register writes that decisions are allowed to see. *)
  let state =
    Pipeline.run_writes t.family.Family.observe state (fun base written ->
      env base written SM.empty)
  in
  (* Stage 2 - decide. Reads the values stage 1 left behind. *)
  let decide = env state SM.empty SM.empty in
  let market = Cascade.select_all t.family.Family.market_rules decide in
  let farmer = Cascade.select_first t.family.Family.farmer_cascade decide in
  (* Stage 3 - commit. May ask which rules fired. *)
  let groups =
    of_map
      [ ( Family.farmer
        , match farmer with
          | None -> []
          | Some firing -> [ firing.Cascade.fired_rule ] )
      ; Family.market, Cascade.fired_names market
      ]
  in
  let state =
    Pipeline.run_writes t.family.Family.commit state (fun base written ->
      env base written groups)
  in
  t.build_action { farmer; market }, state
;;

(* One interpreter plus one player's register bank, shaped like a policy. *)
module Policy = struct
  type ('obs, 'action) policy =
    { interpreter : ('obs, 'action) t
    ; mutable registers : value SM.t
    }

  let create interpreter = { interpreter; registers = initial_registers interpreter }
  let reset policy = policy.registers <- initial_registers policy.interpreter

  let act policy observation =
    let action, registers = step policy.interpreter observation policy.registers in
    policy.registers <- registers;
    action
  ;;

  (* Final register values, in declaration order, for reports and fixtures. *)
  let snapshot policy =
    List.map
      (fun name -> name, SM.find name policy.registers)
      policy.interpreter.family.Family.register_order
  ;;
end
