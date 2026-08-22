(* Role 1 of the policy DSL, on the reading side: the expression language.

   The counterpart of submission/dsl/expr.py, and deliberately not the counterpart of
   authoring/lib/expr.ml. Those two OCaml files point in opposite directions: the
   authoring GADT builds a family that is well-kinded by construction and emits JSON,
   while this module reads JSON that arrives already written and must therefore re-derive
   every kind at load time. The same rules, checked by a pass instead of by the compiler.

   Like the Python original this module knows about integers, booleans, and enum strings,
   and nothing about farming, registers, or stages beyond the names it is handed. *)

module SS = Set.Make (String)
module SM = Map.Make (String)

(* A malformed or ill-typed encoding, reported with the path that failed — the same
   "where: what" shape submission/dsl/expr.py's DslError carries, so a family rejected by
   one backend is rejected with a recognisable message by the other. *)
exception Dsl_error of string * string

let fail path message = raise (Dsl_error (path, message))
let failf path fmt = Printf.ksprintf (fail path) fmt

let error_message = function
  | Dsl_error (path, message) -> Printf.sprintf "%s: %s" path message
  | exn -> Printexc.to_string exn
;;

(* ------------------------------------------------------------------ *)
(* Kinds *)
(* ------------------------------------------------------------------ *)

type base =
  | Int
  | Bool
  | Str

(* [values] narrows a string to an enum domain. Comparing two disjoint domains is rejected
   at load time, which is what catches a misspelled state name such as "LIQUIDATON" before
   it silently evaluates to false for an entire episode. *)
type kind =
  { base : base
  ; values : SS.t option
  }

let int_kind = { base = Int; values = None }
let bool_kind = { base = Bool; values = None }
let str_kind = { base = Str; values = None }
let enum_kind values = { base = Str; values = Some (SS.of_list values) }

let base_name = function
  | Int -> "int"
  | Bool -> "bool"
  | Str -> "str"
;;

let kind_name kind =
  match kind.values with
  | None -> base_name kind.base
  | Some values ->
    Printf.sprintf
      "%s{%s}"
      (base_name kind.base)
      (String.concat ", " (SS.elements values))
;;

type value =
  | Vint of int
  | Vbool of bool
  | Vstr of string

let value_name = function
  | Vint n -> string_of_int n
  | Vbool b -> string_of_bool b
  | Vstr s -> Printf.sprintf "'%s'" s
;;

let value_json = function
  | Vint n -> `Int n
  | Vbool b -> `Bool b
  | Vstr s -> `String s
;;

type stage =
  | Reset
  | Observe
  | Decide
  | Commit

let stage_name = function
  | Reset -> "reset"
  | Observe -> "observe"
  | Decide -> "decide"
  | Commit -> "commit"
;;

(* ------------------------------------------------------------------ *)
(* AST *)
(* ------------------------------------------------------------------ *)

type t =
  | Const of value
  | Param of string
  | State of string (* a register as of the start of the current stage *)
  | Next of string (* a register written earlier in this stage's declaration order *)
  | Obs of string
  | Fired of string (* which rule fired in a first-match-wins cascade *)
  | Fired_any of string * string (* whether a named rule fired in an all-match group *)
  | Op of string * t list

(* ------------------------------------------------------------------ *)
(* Parsing *)
(* ------------------------------------------------------------------ *)

let arithmetic = [ "+"; "-"; "*"; "min"; "max" ]
let ordering = [ "<"; "<="; ">"; ">=" ]
let equality = [ "=="; "!=" ]
let nary_logic = [ "and"; "or" ]

let fixed_arity =
  List.concat
    [ List.map (fun op -> op, 2) arithmetic
    ; List.map (fun op -> op, 2) ordering
    ; List.map (fun op -> op, 2) equality
    ; [ "not", 1; "if", 3 ]
    ]
;;

let leaf_arity =
  [ "const", 1; "param", 1; "state", 1; "next", 1; "obs", 1; "fired", 1; "fired?", 2 ]
;;

let check_arity head operands want path =
  let got = List.length operands in
  if got <> want then failf path "'%s' takes %d operand(s), got %d" head want got
;;

(* The DSL has no float and no null literal. docs/policy_dsl.md's worked encoding writes
   ["const", null] and ["const", 0.0], but its own leaf specification admits only int,
   bool, and string; the document is the thing that is wrong, and the Python parser refuses
   both for the same reason this one does. *)
let parse_const operand path =
  match operand with
  | `Int n -> Const (Vint n)
  | `Bool b -> Const (Vbool b)
  | `String s -> Const (Vstr s)
  | other ->
    failf
      path
      "const must be an int, bool, or string; got %s. The DSL has no float or null \
       literal."
      (Yojson.Safe.to_string other)
;;

let parse_name head operand path =
  match operand with
  | `String name -> name
  | other ->
    failf path "'%s' takes a string name, got %s" head (Yojson.Safe.to_string other)
;;

let parse_leaf head operands path =
  match head, operands with
  | "const", [ operand ] -> parse_const operand path
  | "param", [ operand ] -> Param (parse_name head operand path)
  | "state", [ operand ] -> State (parse_name head operand path)
  | "next", [ operand ] -> Next (parse_name head operand path)
  | "obs", [ operand ] -> Obs (parse_name head operand path)
  | "fired", [ operand ] -> Fired (parse_name head operand path)
  | "fired?", [ group; rule ] ->
    Fired_any (parse_name head group path, parse_name "fired?" rule path)
  | _ -> failf path "malformed leaf '%s'" head
;;

let rec parse ?(path = "expr") (node : Yojson.Safe.t) =
  match node with
  | `List (head :: operands) ->
    (match head with
     | `String head -> parse_head head operands path
     | other ->
       failf path "expression head must be a string, got %s" (Yojson.Safe.to_string other))
  | other ->
    failf path "expected a non-empty JSON array, got %s" (Yojson.Safe.to_string other)

and parse_head head operands path =
  match List.assoc_opt head leaf_arity with
  | Some want ->
    check_arity head operands want path;
    parse_leaf head operands path
  | None ->
    if List.mem head nary_logic
    then (
      if operands = [] then failf path "'%s' needs at least one operand" head;
      Op (head, parse_args operands path head))
    else (
      match List.assoc_opt head fixed_arity with
      | Some want ->
        check_arity head operands want path;
        Op (head, parse_args operands path head)
      | None -> failf path "unknown expression head '%s'" head)

and parse_args operands path head =
  List.mapi
    (fun index operand ->
      parse ~path:(Printf.sprintf "%s.%s[%d]" path head index) operand)
    operands
;;

(* ------------------------------------------------------------------ *)
(* Kind inference *)
(* ------------------------------------------------------------------ *)

(* [stage] and [next_available] carry the stage restrictions from docs/policy_dsl.md:
   ["next", r] is legal only in observe and commit and only for a register written earlier
   in that same stage, and the fired leaves are legal only in commit. *)
type type_env =
  { params : kind SM.t
  ; registers : kind SM.t
  ; observations : kind SM.t
  ; stage : stage
  ; next_available : SS.t
  ; groups : SS.t SM.t
  }

let lookup table name what path =
  match SM.find_opt name table with
  | Some kind -> kind
  | None ->
    let known =
      match SM.bindings table with
      | [] -> "<none>"
      | bindings -> String.concat ", " (List.map fst bindings)
    in
    failf path "unknown %s '%s'. Declared: %s" what name known
;;

let lookup_group groups group path =
  match SM.find_opt group groups with
  | Some rules -> rules
  | None ->
    let known =
      match SM.bindings groups with
      | [] -> "<none>"
      | bindings -> String.concat ", " (List.map fst bindings)
    in
    failf path "unknown rule group '%s'. Declared: %s" group known
;;

let check_commit_only env path what =
  if env.stage <> Commit
  then
    failf
      path
      "%s is only available in the commit stage, not %s"
      what
      (stage_name env.stage)
;;

let require_all kinds want op path =
  List.iteri
    (fun index kind ->
      if kind.base <> want
      then
        failf
          path
          "'%s' operand %d must be %s, got %s"
          op
          index
          (base_name want)
          (kind_name kind))
    kinds
;;

let join left right =
  match left.values, right.values with
  | Some a, Some b -> { base = left.base; values = Some (SS.union a b) }
  | _ -> { base = left.base; values = None }
;;

let rec infer ?(path = "expr") expr env =
  match expr with
  | Const (Vbool _) -> bool_kind
  | Const (Vint _) -> int_kind
  | Const (Vstr s) -> { base = Str; values = Some (SS.singleton s) }
  | Param name -> lookup env.params name "parameter" path
  | State name -> lookup env.registers name "register" path
  | Next name ->
    if env.stage <> Observe && env.stage <> Commit
    then failf path "['next', ...] is not allowed in the %s stage" (stage_name env.stage);
    if not (SS.mem name env.next_available)
    then
      failf path "['next', '%s'] must name a register written earlier in this stage" name;
    lookup env.registers name "register" path
  | Obs name -> lookup env.observations name "observation" path
  | Fired group ->
    check_commit_only env path "['fired', ...]";
    let rules = lookup_group env.groups group path in
    (* The empty string is what a group with no matching rule yields. *)
    { base = Str; values = Some (SS.add "" rules) }
  | Fired_any (group, rule) ->
    check_commit_only env path "['fired?', ...]";
    let rules = lookup_group env.groups group path in
    if not (SS.mem rule rules)
    then failf path "group '%s' has no rule named '%s'" group rule;
    bool_kind
  | Op (op, args) -> infer_op op args env path

and infer_op op args env path =
  let kinds =
    List.mapi
      (fun index arg -> infer ~path:(Printf.sprintf "%s.%s[%d]" path op index) arg env)
      args
  in
  if List.mem op arithmetic
  then (
    require_all kinds Int op path;
    int_kind)
  else if List.mem op ordering
  then (
    require_all kinds Int op path;
    bool_kind)
  else if List.mem op nary_logic || op = "not"
  then (
    require_all kinds Bool op path;
    bool_kind)
  else if List.mem op equality
  then (
    match kinds with
    | [ left; right ] ->
      if left.base <> right.base
      then
        failf
          path
          "'%s' compares %s with %s; kinds must match"
          op
          (kind_name left)
          (kind_name right);
      (match left.base, left.values, right.values with
       | Str, Some a, Some b when SS.is_empty (SS.inter a b) ->
         failf
           path
           "'%s' compares disjoint enum domains %s and %s; this comparison can never be \
            true"
           op
           (kind_name left)
           (kind_name right)
       | _ -> ());
      bool_kind
    | _ -> failf path "'%s' takes two operands" op)
  else if op = "if"
  then (
    match kinds with
    | [ cond; then_; else_ ] ->
      if cond.base <> Bool
      then failf path "'if' condition must be bool, got %s" (kind_name cond);
      if then_.base <> else_.base
      then
        failf
          path
          "'if' branches disagree: %s versus %s"
          (kind_name then_)
          (kind_name else_);
      join then_ else_
    | _ -> failf path "'if' takes three operands")
  else failf path "unknown operator '%s'" op
;;

(* ------------------------------------------------------------------ *)
(* Evaluation *)
(* ------------------------------------------------------------------ *)

(* [state] is the register snapshot taken at the start of the stage, and [next] holds only
   the registers already written within it. Keeping them separate is what makes
   simultaneous commit unambiguous: an expression can only see an in-stage write by asking
   for it explicitly. *)
type env =
  { e_params : value SM.t
  ; e_state : value SM.t
  ; e_observe : string -> value
  ; e_next : value SM.t
  ; e_groups : string list SM.t
  }

let empty_env ~params ~state ~observe =
  { e_params = params
  ; e_state = state
  ; e_observe = observe
  ; e_next = SM.empty
  ; e_groups = SM.empty
  }
;;

let bound table name what =
  match SM.find_opt name table with
  | Some value -> value
  | None -> fail "eval" (Printf.sprintf "unbound %s '%s'" what name)
;;

let as_int op = function
  | Vint n -> n
  | other -> failf "eval" "'%s' expected an int, got %s" op (value_name other)
;;

let as_bool op = function
  | Vbool b -> b
  | other -> failf "eval" "'%s' expected a bool, got %s" op (value_name other)
;;

(* Assumes [infer] has already accepted the expression against a matching type_env; this
   performs no type checking of its own. *)
let rec evaluate expr env =
  match expr with
  | Const value -> value
  | Param name -> bound env.e_params name "parameter"
  | State name -> bound env.e_state name "register"
  | Next name -> bound env.e_next name "register"
  | Obs name -> env.e_observe name
  | Fired group ->
    (match SM.find_opt group env.e_groups with
     | Some (first :: _) -> Vstr first
     | _ -> Vstr "")
  | Fired_any (group, rule) ->
    (match SM.find_opt group env.e_groups with
     | Some fired -> Vbool (List.mem rule fired)
     | None -> Vbool false)
  | Op (op, args) -> evaluate_op op args env

(* Short-circuit forms are evaluated before anything else touches operands, because guards
   such as harvest_ready rely on [and] to keep an accessor from being asked for a field the
   tile does not have. *)
and evaluate_op op args env =
  match op, args with
  | "and", args -> Vbool (List.for_all (fun arg -> as_bool "and" (evaluate arg env)) args)
  | "or", args -> Vbool (List.exists (fun arg -> as_bool "or" (evaluate arg env)) args)
  | "if", [ cond; then_; else_ ] ->
    evaluate (if as_bool "if" (evaluate cond env) then then_ else else_) env
  | "not", [ arg ] -> Vbool (not (as_bool "not" (evaluate arg env)))
  | _, [ left; right ] ->
    let left = evaluate left env
    and right = evaluate right env in
    binary op left right
  | _ -> failf "eval" "unknown operator '%s'" op

and binary op left right =
  let ints combine = Vint (combine (as_int op left) (as_int op right)) in
  let compares combine = Vbool (combine (as_int op left) (as_int op right)) in
  match op with
  | "+" -> ints ( + )
  | "-" -> ints ( - )
  | "*" -> ints ( * )
  | "min" -> ints min
  | "max" -> ints max
  | "<" -> compares ( < )
  | "<=" -> compares ( <= )
  | ">" -> compares ( > )
  | ">=" -> compares ( >= )
  | "==" -> Vbool (left = right)
  | "!=" -> Vbool (left <> right)
  | _ -> failf "eval" "unknown operator '%s'" op
;;

(* ------------------------------------------------------------------ *)
(* Values crossing in from outside *)
(* ------------------------------------------------------------------ *)

(* The one runtime type check the interpreter performs, shared by the observation resolver
   and by every literal read out of the family artifact. *)
let check_value kind value path =
  match kind.base, value with
  | Bool, Vbool _ -> value
  | Int, Vint _ -> value
  | Str, Vstr s ->
    (match kind.values with
     | Some values when not (SS.mem s values) ->
       failf path "'%s' is not one of %s" s (String.concat ", " (SS.elements values))
     | _ -> value)
  | base, got -> failf path "expected %s, got %s" (base_name base) (value_name got)
;;
