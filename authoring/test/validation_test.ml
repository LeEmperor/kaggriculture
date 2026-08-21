(* What Family.create must refuse — the residual checks the GADT cannot carry.

   Each case is a family that is well-kinded (it compiles) but violates a rule
   submission/dsl/family.py enforces at load time. The authoring side must reject it
   before any JSON exists, so a family author hears about the mistake from OCaml rather
   than from a Python traceback later. The positive case is
   Families.Monocrop_reorder.family itself: forcing it proves the one real family still
   validates. *)

open Policy_family
open Expr.O

let failures = ref 0

let expect_failure name thunk =
  match thunk () with
  | (_ : Family.t) ->
    incr failures;
    Printf.printf "FAIL %s: validation accepted a bad family\n" name
  | exception Failure message -> Printf.printf "PASS %s (%s)\n" name message
;;

let counter = Expr.Reg.int "counter" ~init:0 ~cls:Expr.Telemetry

let colour =
  Expr.Reg.enum "colour" ~values:[ "RED"; "GREEN" ] ~init:"RED" ~cls:Expr.Decision
;;

let minimal
  ?(registers = [ Family.R counter ])
  ?(reset_when = bool false)
  ?(observe = [])
  ?(market_rules = [])
  ?(farmer_cascade = [])
  ?(commit = [])
  ()
  =
  Family.create
    ~policy_id:"test-v1"
    ~family:"test"
    ~family_version:1
    ~parameters:[]
    ~registers
    ~reset_when
    ~observe
    ~market_rules
    ~farmer_cascade
    ~commit
;;

let () =
  expect_failure "next outside observe/commit" (fun () ->
    minimal ~reset_when:(next counter >: int 0) ());
  expect_failure "next reads a not-yet-written register" (fun () ->
    minimal ~observe:[ Family.write counter (next counter +: int 1) ] ());
  expect_failure "fired outside commit" (fun () ->
    minimal
      ~observe:
        [ Family.write counter (if_ (fired Expr.Farmer ==: str "x") (int 1) (int 0)) ]
      ());
  expect_failure "fired? names a rule that does not exist" (fun () ->
    minimal
      ~commit:
        [ Family.write
            counter
            (if_ (fired_any Expr.Market "no_such_rule") (int 1) (int 0))
        ]
      ());
  expect_failure "double write within a stage" (fun () ->
    minimal ~commit:[ Family.write counter (int 1); Family.write counter (int 2) ] ());
  expect_failure "enum write outside the declared domain" (fun () ->
    minimal ~registers:[ Family.R colour ] ~commit:[ Family.write colour (str "BLUE") ] ());
  expect_failure "undeclared register referenced" (fun () ->
    minimal ~registers:[ Family.R colour ] ~reset_when:(state counter >: int 0) ());
  expect_failure "duplicate rule name" (fun () ->
    minimal
      ~farmer_cascade:
        [ Family.rule ~name:"twice" ~when_:(bool true) ~emit:(Family.emit "PASS" [])
        ; Family.rule ~name:"twice" ~when_:(bool false) ~emit:(Family.emit "PASS" [])
        ]
      ());
  expect_failure "no registers" (fun () -> minimal ~registers:[] ());
  (* The positive case: the real family validates and emits. *)
  let json = Family.to_json Families.Monocrop_reorder.family in
  let vector_count = String.length (Yojson.Safe.to_string json) in
  Printf.printf "PASS monocrop_reorder validates and emits (%d bytes)\n" vector_count;
  if !failures > 0 then exit 1
;;
