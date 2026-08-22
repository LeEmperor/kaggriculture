(* Role 3 of the policy DSL: the staged register machine.

   Within a stage every right-hand side is evaluated against the register values as they
   stood at the *start* of that stage, and all writes land together. This is Verilog's
   non-blocking assignment, and it is here for the same reason it is there: it removes any
   question of whether a register had already been updated when another expression read it.
   ["next", reg] is the one deliberate exception.

   The decide stage is not modelled here — it produces actions rather than register writes,
   and the interpreter runs it between run_writes calls. *)

type register_class =
  | Decision
  | Telemetry

(* [cls] records the audit from docs/policy_dsl.md: a decision register is read by some
   guard and therefore enters the cross-backend semantic contract, while a telemetry
   register is written but never read by a decision and may diverge harmlessly. Golden
   vectors compare the first strictly and the second loosely. *)
type register =
  { name : string
  ; kind : Expr.kind
  ; init : Expr.value
  ; cls : register_class
  }

type write =
  { reg : string
  ; value : Expr.t
  }

let class_name = function
  | Decision -> "decision"
  | Telemetry -> "telemetry"
;;

(* The register bank at episode start, and what stage 0 restores. *)
let initial_registers registers =
  List.fold_left
    (fun bank register -> Expr.SM.add register.name register.init bank)
    Expr.SM.empty
    registers
;;

(* Apply one stage's writes simultaneously and return the new register bank. [make_env] is
   handed the frozen start-of-stage state and the growing map of in-stage writes, so this
   module never needs to know what else an environment carries. *)
let run_writes writes state make_env =
  let written =
    List.fold_left
      (fun written write ->
        let value = Expr.evaluate write.value (make_env state written) in
        Expr.SM.add write.reg value written)
      Expr.SM.empty
      writes
  in
  Expr.SM.union (fun _ _ latest -> Some latest) state written
;;
