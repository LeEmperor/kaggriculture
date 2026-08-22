(* Role 2 of the policy DSL: rule groups.

   Two selection disciplines over the same rule shape. [select_first] is first-match-wins:
   one rule fires, or none, which is what lets a nested chain of conditionals in
   hand-written code flatten into a flat guarded list without changing which branch runs.
   [select_all] fires every match in declaration order, for groups whose members are
   independent decisions rather than alternatives.

   Emit operands are evaluated at selection time, in the same environment as the guard, so
   a rule cannot observe a register write made after it fired. *)

type emit =
  { op : string
  ; operands : Expr.t list
  }

type rule =
  { name : string
  ; when_ : Expr.t
  ; emit : emit
  }

(* A rule that matched, with its operands already evaluated. *)
type firing =
  { fired_rule : string
  ; fired_op : string
  ; fired_operands : Expr.value list
  }

let fire rule env =
  { fired_rule = rule.name
  ; fired_op = rule.emit.op
  ; fired_operands =
      List.map (fun operand -> Expr.evaluate operand env) rule.emit.operands
  }
;;

let matches rule env =
  match Expr.evaluate rule.when_ env with
  | Expr.Vbool b -> b
  | other ->
    Expr.failf
      "eval"
      "guard of rule '%s' produced %s, not a boolean"
      rule.name
      (Expr.value_name other)
;;

let select_first rules env =
  match List.find_opt (fun rule -> matches rule env) rules with
  | None -> None
  | Some rule -> Some (fire rule env)
;;

let select_all rules env =
  List.filter_map
    (fun rule -> if matches rule env then Some (fire rule env) else None)
    rules
;;

(* Rule names in firing order, as the commit stage's fired leaves see them. *)
let fired_names firings = List.map (fun firing -> firing.fired_rule) firings
