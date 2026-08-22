(* Role 4 of the policy DSL: the family artifact.

   [load] turns a family encoding into a validated, immutable value. Every check that can
   be made without an observation is made here, once, at load time: name resolution for
   parameters, registers, and observations; the stage restrictions on next and fired; kind
   agreement between each write and the register it targets; emit arity and operand kinds;
   and enum domains.

   Doing all of it up front is the point, and it is the same list submission/dsl/family.py
   works through — a family that loads is one whose evaluation cannot fail on a name or a
   type at turn 400. Both game-specific vocabularies are injected, so this module names no
   crop and no action. *)

open Expr

let dsl_version = 1
let farmer = "farmer"
let market = "market"

let top_level =
  [ "$schema"
  ; "policy_id"
  ; "family"
  ; "family_version"
  ; "dsl_version"
  ; "parameters"
  ; "registers"
  ; "reset_when"
  ; "observe"
  ; "market_rules"
  ; "farmer_cascade"
  ; "commit"
  ]
;;

(* One configuration register, set before the episode and never written. *)
type parameter_spec =
  { spec_name : string
  ; spec_kind : kind
  ; minimum : int option
  ; maximum : int option
  }

type t =
  { policy_id : string
  ; family_name : string
  ; family_version : int
  ; encoding_version : int
  ; parameter_order : string list
  ; parameters : parameter_spec SM.t
  ; register_order : string list (* declaration order; the fixture vocabulary's order *)
  ; registers : Pipeline.register SM.t
  ; reset_when : Expr.t
  ; observe : Pipeline.write list
  ; market_rules : Cascade.rule list
  ; farmer_cascade : Cascade.rule list
  ; commit : Pipeline.write list
  }

(* ------------------------------------------------------------------ *)
(* JSON access *)
(* ------------------------------------------------------------------ *)

let assoc_fields json path =
  match json with
  | `Assoc fields -> fields
  | other -> failf path "expected an object, got %s" (Yojson.Safe.to_string other)
;;

let list_items json path =
  match json with
  | `List items -> items
  | other -> failf path "expected an array, got %s" (Yojson.Safe.to_string other)
;;

let require fields key path =
  match List.assoc_opt key fields with
  | Some value -> value
  | None -> failf path "missing '%s'" key
;;

let require_string fields key path =
  match require fields key path with
  | `String s -> s
  | other -> failf path "%s must be a string, got %s" key (Yojson.Safe.to_string other)
;;

let require_int fields key path =
  match require fields key path with
  | `Int n -> n
  | other -> failf path "%s must be an integer, got %s" key (Yojson.Safe.to_string other)
;;

let optional_int fields key path =
  match List.assoc_opt key fields with
  | None -> None
  | Some (`Int n) -> Some n
  | Some other ->
    failf path "%s must be an integer, got %s" key (Yojson.Safe.to_string other)
;;

let value_of_json kind json path =
  match kind.base, json with
  | Bool, `Bool b -> Vbool b
  | Int, `Int n -> Vint n
  | Str, `String s -> check_value kind (Vstr s) path
  | base, other ->
    failf path "expected %s, got %s" (base_name base) (Yojson.Safe.to_string other)
;;

(* ------------------------------------------------------------------ *)
(* Declarations *)
(* ------------------------------------------------------------------ *)

let declared_kind fields path =
  match require_string fields "type" path with
  | "int" -> int_kind
  | "bool" -> bool_kind
  | "enum" ->
    let values =
      List.map
        (function
          | `String value -> value
          | _ -> failf path "enum 'values' must be a non-empty list of strings")
        (list_items (require fields "values" path) path)
    in
    if values = [] then failf path "enum 'values' must be a non-empty list of strings";
    enum_kind values
  | other -> failf path "unknown type '%s'; the DSL has int, bool, and enum only" other
;;

let load_parameters document =
  let block = assoc_fields (require document "parameters" "family") "family.parameters" in
  List.map
    (fun (name, raw) ->
      let path = "parameters." ^ name in
      let fields = assoc_fields raw path in
      let kind = declared_kind fields path in
      if kind.base = Bool then fail path "boolean parameters are not supported";
      let minimum = optional_int fields "min" path
      and maximum = optional_int fields "max" path in
      (match minimum, maximum with
       | Some low, Some high when low > high ->
         failf path "min %d exceeds max %d" low high
       | _ -> ());
      name, { spec_name = name; spec_kind = kind; minimum; maximum })
    block
;;

let load_registers document =
  let block = assoc_fields (require document "registers" "family") "family.registers" in
  if block = [] then fail "registers" "a family needs at least one register";
  List.map
    (fun (name, raw) ->
      let path = "registers." ^ name in
      let fields = assoc_fields raw path in
      let kind = declared_kind fields path in
      let cls =
        match require_string fields "class" path with
        | "decision" -> Pipeline.Decision
        | "telemetry" -> Pipeline.Telemetry
        | other -> failf path "class must be one of decision, telemetry; got '%s'" other
      in
      let init = value_of_json kind (require fields "init" path) (path ^ ".init") in
      name, { Pipeline.name; kind; init; cls })
    block
;;

(* ------------------------------------------------------------------ *)
(* Stages *)
(* ------------------------------------------------------------------ *)

let check_assignable (register : Pipeline.register) kind path =
  if kind.base <> register.kind.base
  then
    failf
      path
      "cannot write %s to register '%s' of type %s"
      (kind_name kind)
      register.name
      (kind_name register.kind);
  match register.kind.values, kind.values with
  | Some declared, Some written ->
    let stray = SS.diff written declared in
    if not (SS.is_empty stray)
    then
      failf
        path
        "writes value(s) %s outside the declared domain of '%s'"
        (String.concat ", " (SS.elements stray))
        register.name
  | _ -> ()
;;

let load_writes document key registers type_env =
  let block = list_items (require document key "family") ("family." ^ key) in
  let stage = if key = "observe" then Observe else Commit in
  let writes, _ =
    List.fold_left
      (fun (writes, written) raw ->
        let path = Printf.sprintf "%s[%d]" key (List.length writes) in
        let fields = assoc_fields raw path in
        if List.sort compare (List.map fst fields) <> [ "reg"; "value" ]
        then fail path "expected an object with exactly 'reg' and 'value'";
        let name = require_string fields "reg" path in
        let register =
          match SM.find_opt name registers with
          | Some register -> register
          | None -> failf path "unknown register '%s'" name
        in
        if SS.mem name written
        then
          failf
            path
            "'%s' is written twice in the %s stage; writes within a stage commit \
             simultaneously, so a second write would be ambiguous"
            name
            (stage_name stage);
        let value_path = path ^ ".value" in
        let value = parse ~path:value_path (require fields "value" path) in
        let kind = infer ~path:value_path value (type_env stage written) in
        check_assignable register kind value_path;
        writes @ [ { Pipeline.reg = name; value } ], SS.add name written)
      ([], SS.empty)
      block
  in
  writes
;;

let load_emit raw signatures tenv path =
  match raw with
  | `List (`String op :: operands) ->
    let expected =
      match SM.find_opt op signatures with
      | Some kinds -> kinds
      | None ->
        let known =
          match SM.bindings signatures with
          | [] -> "<none>"
          | bindings -> String.concat ", " (List.map fst bindings)
        in
        failf (path ^ ".emit") "unknown action '%s'. Available: %s" op known
    in
    if List.length operands <> List.length expected
    then
      failf
        (path ^ ".emit")
        "'%s' takes %d operand(s), got %d"
        op
        (List.length expected)
        (List.length operands);
    let parsed =
      List.mapi
        (fun index (operand, want) ->
          let where = Printf.sprintf "%s.emit[%d]" path (index + 1) in
          let expression = parse ~path:where operand in
          let got = infer ~path:where expression tenv in
          if got.base <> want.base
          then
            failf
              where
              "'%s' operand %d must be %s, got %s"
              op
              index
              (kind_name want)
              (kind_name got);
          expression)
        (List.combine operands expected)
    in
    { Cascade.op; operands = parsed }
  | other ->
    failf
      (path ^ ".emit")
      "expected [op, ...operands], got %s"
      (Yojson.Safe.to_string other)
;;

let load_rules document key group emits tenv =
  let block = list_items (require document key "family") ("family." ^ key) in
  let signatures =
    match SM.find_opt group emits with
    | Some signatures -> signatures
    | None -> failf key "no emit vocabulary supplied for group '%s'" group
  in
  let rules, _ =
    List.fold_left
      (fun (rules, seen) raw ->
        let path = Printf.sprintf "%s[%d]" key (List.length rules) in
        let fields = assoc_fields raw path in
        if List.sort compare (List.map fst fields) <> [ "emit"; "name"; "when" ]
        then fail path "expected an object with exactly 'name', 'when', and 'emit'";
        let name = require_string fields "name" path in
        if name = "" then fail path "'name' must be a non-empty string";
        if SS.mem name seen then failf path "duplicate rule name '%s'" name;
        let when_path = path ^ ".when" in
        let when_ = parse ~path:when_path (require fields "when" path) in
        if (infer ~path:when_path when_ tenv).base <> Bool
        then fail when_path "a guard must be a boolean expression";
        let emit = load_emit (require fields "emit" path) signatures tenv path in
        rules @ [ { Cascade.name; when_; emit } ], SS.add name seen)
      ([], SS.empty)
      block
  in
  rules
;;

(* Rule names, read before the rules themselves are validated: the fired leaves in the
   commit stage need the name set to type-check against, and commit is loaded first. *)
let rule_names document key =
  List.fold_left
    (fun names raw ->
      match raw with
      | `Assoc fields ->
        (match List.assoc_opt "name" fields with
         | Some (`String name) -> SS.add name names
         | _ -> names)
      | _ -> names)
    SS.empty
    (list_items (require document key "family") ("family." ^ key))
;;

(* ------------------------------------------------------------------ *)
(* Loading *)
(* ------------------------------------------------------------------ *)

let of_map pairs =
  List.fold_left (fun map (key, value) -> SM.add key value map) SM.empty pairs
;;

let load json ~observations ~emits =
  let document = assoc_fields json "family" in
  let unknown =
    List.sort
      compare
      (List.filter (fun (key, _) -> not (List.mem key top_level)) document |> List.map fst)
  in
  if unknown <> []
  then failf "family" "unknown top-level keys: %s" (String.concat ", " unknown);
  let encoding_version = require_int document "dsl_version" "family" in
  if encoding_version <> dsl_version
  then
    failf
      "family.dsl_version"
      "this interpreter implements dsl_version %d, the encoding declares %d"
      dsl_version
      encoding_version;
  let parameters = load_parameters document in
  let registers = load_registers document in
  let parameter_map = of_map parameters
  and register_map = of_map registers in
  let groups =
    of_map
      [ farmer, rule_names document "farmer_cascade"
      ; market, rule_names document "market_rules"
      ]
  in
  let param_kinds = SM.map (fun spec -> spec.spec_kind) parameter_map
  and register_kinds =
    SM.map (fun (register : Pipeline.register) -> register.kind) register_map
  in
  let type_env stage next_available =
    { params = param_kinds
    ; registers = register_kinds
    ; observations
    ; stage
    ; next_available
    ; groups
    }
  in
  let reset_when = parse ~path:"reset_when" (require document "reset_when" "family") in
  if (infer ~path:"reset_when" reset_when (type_env Reset SS.empty)).base <> Bool
  then fail "reset_when" "must be a boolean expression";
  let observe = load_writes document "observe" register_map type_env in
  let commit = load_writes document "commit" register_map type_env in
  let decide = type_env Decide SS.empty in
  let farmer_cascade = load_rules document "farmer_cascade" farmer emits decide in
  let market_rules = load_rules document "market_rules" market emits decide in
  { policy_id = require_string document "policy_id" "family"
  ; family_name = require_string document "family" "family"
  ; family_version = require_int document "family_version" "family"
  ; encoding_version
  ; parameter_order = List.map fst parameters
  ; parameters = parameter_map
  ; register_order = List.map fst registers
  ; registers = register_map
  ; reset_when
  ; observe
  ; market_rules
  ; farmer_cascade
  ; commit
  }
;;

let register_list family =
  List.map (fun name -> SM.find name family.registers) family.register_order
;;

(* Registers a guard reads, and so the ones backends must agree on. *)
let decision_registers family =
  List.filter
    (fun name -> (SM.find name family.registers).Pipeline.cls = Pipeline.Decision)
    family.register_order
;;

(* Every observation this family reads, across all four stages. The interpreter uses it to
   check an accessor's parameter dependencies before the episode starts, so a vocabulary
   entry needing a parameter the family never declared is a load-time error rather than a
   turn-0 lookup failure. *)
let observation_names family =
  let rec collect expr names =
    match expr with
    | Obs name -> SS.add name names
    | Op (_, args) -> List.fold_left (fun names arg -> collect arg names) names args
    | _ -> names
  in
  let names = collect family.reset_when SS.empty in
  let names =
    List.fold_left
      (fun names (write : Pipeline.write) -> collect write.value names)
      names
      (family.observe @ family.commit)
  in
  List.fold_left
    (fun names (rule : Cascade.rule) ->
      let names = collect rule.when_ names in
      List.fold_left (fun names operand -> collect operand names) names rule.emit.operands)
    names
    (family.market_rules @ family.farmer_cascade)
;;

(* ------------------------------------------------------------------ *)
(* Candidate binding *)
(* ------------------------------------------------------------------ *)

let check_parameter spec json =
  let path = "parameters." ^ spec.spec_name in
  let value = value_of_json spec.spec_kind json path in
  (match spec.spec_kind.base, value with
   | Int, Vint n ->
     (match spec.minimum with
      | Some low when n < low -> failf path "%d is below the minimum %d" n low
      | _ -> ());
     (match spec.maximum with
      | Some high when n > high -> failf path "%d is above the maximum %d" n high
      | _ -> ())
   | _ -> ());
  value
;;

(* Validate a candidate's parameter block against the declared schema. *)
let bind family json =
  let supplied = assoc_fields json "parameters" in
  let declared = SS.of_list family.parameter_order in
  let given = SS.of_list (List.map fst supplied) in
  let missing = SS.diff declared given in
  if not (SS.is_empty missing)
  then failf "parameters" "missing: %s" (String.concat ", " (SS.elements missing));
  let extra = SS.diff given declared in
  if not (SS.is_empty extra)
  then failf "parameters" "unknown: %s" (String.concat ", " (SS.elements extra));
  List.fold_left
    (fun bound name ->
      let spec = SM.find name family.parameters in
      SM.add name (check_parameter spec (List.assoc name supplied)) bound)
    SM.empty
    family.parameter_order
;;

(* ------------------------------------------------------------------ *)
(* The register bank as JSON *)
(* ------------------------------------------------------------------ *)

(* The wire form of a register bank, and the vocabulary the golden fixtures are written in:
   one object keyed by register name, in declaration order. Reading one back is a
   type-checked crossing like any other value arriving from outside, so a fixture recorded
   against a different family shape fails here rather than mid-turn. *)
let registers_to_json family bank =
  `Assoc
    (List.map (fun name -> name, value_json (SM.find name bank)) family.register_order)
;;

let registers_of_json family json =
  let supplied = assoc_fields json "registers" in
  let declared = SS.of_list family.register_order in
  let given = SS.of_list (List.map fst supplied) in
  let missing = SS.diff declared given
  and extra = SS.diff given declared in
  if not (SS.is_empty missing)
  then failf "registers" "missing: %s" (String.concat ", " (SS.elements missing));
  if not (SS.is_empty extra)
  then failf "registers" "unknown: %s" (String.concat ", " (SS.elements extra));
  List.fold_left
    (fun bank name ->
      let register = SM.find name family.registers in
      let path = "registers." ^ name in
      SM.add
        name
        (value_of_json register.Pipeline.kind (List.assoc name supplied) path)
        bank)
    SM.empty
    family.register_order
;;
