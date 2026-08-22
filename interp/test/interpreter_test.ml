(* What the reading side must accept and what it must refuse.

   The cross-backend gate is elsewhere and is the real one: [python3 -m experiments.golden
   check --backend ocaml] replays the 134 checked-in vectors through this interpreter, and
   [sweep --backend ocaml] compares it turn by turn against the hand-written policy over
   whole episodes. Neither can run here — the fixtures live under experiments/, which dune
   deliberately does not traverse.

   So this file covers what that gate cannot see: the load-time refusals. A family that
   loads is supposed to be one whose evaluation cannot fail on a name or a type at turn 400,
   which is a claim about the encodings that are *rejected*, and no accepted family
   exercises it. Each case below is JSON that must not survive Family.load, mirroring the
   negative suite in authoring/test/validation_test.ml on the writing side. *)

open Policy_dsl

let failures = ref 0

let report ok name detail =
  if ok
  then Printf.printf "PASS %s (%s)\n" name detail
  else (
    incr failures;
    Printf.printf "FAIL %s: %s\n" name detail)
;;

(* ------------------------------------------------------------------ *)
(* A minimal family to mutate *)
(* ------------------------------------------------------------------ *)

let observations = Expr.SM.of_list [ "day", Expr.int_kind; "wet", Expr.bool_kind ]

let emits =
  Expr.SM.of_list
    [ Family.farmer, Expr.SM.of_list [ "PASS", []; "PLANT", [ Expr.str_kind ] ]
    ; Family.market, Expr.SM.of_list [ "SELL", [ Expr.str_kind; Expr.int_kind ] ]
    ]
;;

let base =
  `Assoc
    [ "policy_id", `String "test-v1"
    ; "family", `String "test"
    ; "family_version", `Int 1
    ; "dsl_version", `Int 1
    ; ( "parameters"
      , `Assoc
          [ "crop", `Assoc [ "type", `String "enum"; "values", `List [ `String "WHEAT" ] ]
          ] )
    ; ( "registers"
      , `Assoc
          [ ( "counter"
            , `Assoc
                [ "type", `String "int"; "init", `Int 0; "class", `String "telemetry" ] )
          ; ( "colour"
            , `Assoc
                [ "type", `String "enum"
                ; "values", `List [ `String "RED"; `String "GREEN" ]
                ; "init", `String "RED"
                ; "class", `String "decision"
                ] )
          ] )
    ; "reset_when", `List [ `String "const"; `Bool false ]
    ; "observe", `List []
    ; "market_rules", `List []
    ; "farmer_cascade", `List []
    ; "commit", `List []
    ]
;;

(* Replace one top-level key, keeping the rest of the minimal family intact. *)
let with_key key value =
  match base with
  | `Assoc fields ->
    `Assoc (List.map (fun (k, v) -> if k = key then k, value else k, v) fields)
  | _ -> assert false
;;

let load json = Family.load json ~observations ~emits

let expect_reject name json =
  match load json with
  | (_ : Family.t) -> report false name "load accepted a bad encoding"
  | exception exn -> report true name (Expr.error_message exn)
;;

let expect_accept name json =
  match load json with
  | (_ : Family.t) -> report true name "loads"
  | exception exn -> report false name (Expr.error_message exn)
;;

let write reg value = `Assoc [ "reg", `String reg; "value", value ]
let rule name when_ emit = `Assoc [ "name", `String name; "when", when_; "emit", emit ]
let const_true = `List [ `String "const"; `Bool true ]
let int_ n = `List [ `String "const"; `Int n ]
let str_ s = `List [ `String "const"; `String s ]
let state r = `List [ `String "state"; `String r ]
let next r = `List [ `String "next"; `String r ]
let obs o = `List [ `String "obs"; `String o ]

(* ------------------------------------------------------------------ *)
(* Load-time refusals *)
(* ------------------------------------------------------------------ *)

let () =
  expect_accept "the minimal family" base;
  expect_reject
    "unknown top-level key"
    (match base with
     | `Assoc fields -> `Assoc (fields @ [ "extra", `Int 1 ])
     | _ -> assert false);
  expect_reject "a future dsl_version" (with_key "dsl_version" (`Int 2));
  expect_reject "no registers" (with_key "registers" (`Assoc []));
  expect_reject "a non-boolean reset_when" (with_key "reset_when" (int_ 1));
  expect_reject
    "an undeclared register"
    (with_key "reset_when" (`List [ `String ">"; state "missing"; int_ 0 ]));
  expect_reject
    "an undeclared observation"
    (with_key "reset_when" (`List [ `String ">"; obs "rainfall"; int_ 0 ]));
  expect_reject
    "next outside observe and commit"
    (with_key "reset_when" (`List [ `String ">"; next "counter"; int_ 0 ]));
  expect_reject
    "next reading a register not yet written in this stage"
    (with_key "observe" (`List [ write "counter" (next "counter") ]));
  expect_accept
    "next reading a register written earlier in this stage"
    (with_key
       "observe"
       (`List
         [ write "counter" (int_ 1)
         ; write
             "colour"
             (`List
               [ `String "if"
               ; `List [ `String ">"; next "counter"; int_ 0 ]
               ; str_ "GREEN"
               ; str_ "RED"
               ])
         ]));
  expect_reject
    "fired outside commit"
    (with_key
       "observe"
       (`List
         [ write
             "colour"
             (`List
               [ `String "if"
               ; `List
                   [ `String "=="; `List [ `String "fired"; `String "farmer" ]; str_ "" ]
               ; str_ "RED"
               ; str_ "GREEN"
               ])
         ]));
  expect_reject
    "fired? naming a rule that does not exist"
    (with_key
       "commit"
       (`List
         [ write
             "counter"
             (`List
               [ `String "if"
               ; `List [ `String "fired?"; `String "market"; `String "no_such_rule" ]
               ; int_ 1
               ; int_ 0
               ])
         ]));
  expect_reject
    "a double write within a stage"
    (with_key "commit" (`List [ write "counter" (int_ 1); write "counter" (int_ 2) ]));
  expect_reject
    "an enum write outside the declared domain"
    (with_key "commit" (`List [ write "colour" (str_ "BLUE") ]));
  expect_reject
    "a kind mismatch between a write and its register"
    (with_key "commit" (`List [ write "counter" const_true ]));
  expect_reject
    "an int operand where a bool is required"
    (with_key "reset_when" (`List [ `String "not"; int_ 1 ]));
  expect_reject
    "mismatched if branches"
    (with_key
       "commit"
       (`List
         [ write "counter" (`List [ `String "if"; const_true; int_ 1; str_ "RED" ]) ]));
  expect_reject
    "a comparison of disjoint enum domains"
    (with_key
       "commit"
       (`List
         [ write
             "counter"
             (`List
               [ `String "if"
               ; `List [ `String "=="; state "colour"; str_ "BLUE" ]
               ; int_ 1
               ; int_ 0
               ])
         ]));
  expect_reject
    "a float literal"
    (with_key
       "reset_when"
       (`List [ `String ">"; obs "day"; `List [ `String "const"; `Float 1.5 ] ]));
  expect_reject
    "a null literal"
    (with_key
       "reset_when"
       (`List [ `String "=="; obs "day"; `List [ `String "const"; `Null ] ]));
  expect_reject
    "an unknown expression head"
    (with_key "reset_when" (`List [ `String "xor"; const_true; const_true ]));
  expect_reject
    "wrong operator arity"
    (with_key "reset_when" (`List [ `String "not"; const_true; const_true ]));
  expect_reject
    "a non-boolean guard"
    (with_key "farmer_cascade" (`List [ rule "bad" (int_ 1) (`List [ `String "PASS" ]) ]));
  expect_reject
    "an unknown action"
    (with_key
       "farmer_cascade"
       (`List [ rule "bad" const_true (`List [ `String "SEL" ]) ]));
  expect_reject
    "an emit missing an operand"
    (with_key
       "farmer_cascade"
       (`List [ rule "bad" const_true (`List [ `String "PLANT" ]) ]));
  expect_reject
    "an emit operand of the wrong kind"
    (with_key
       "farmer_cascade"
       (`List [ rule "bad" const_true (`List [ `String "PLANT"; int_ 1 ]) ]));
  expect_reject
    "a duplicate rule name"
    (with_key
       "farmer_cascade"
       (`List
         [ rule "twice" const_true (`List [ `String "PASS" ])
         ; rule "twice" const_true (`List [ `String "PASS" ])
         ]));
  expect_reject
    "a market emit in the farmer cascade"
    (with_key
       "farmer_cascade"
       (`List [ rule "bad" const_true (`List [ `String "SELL"; str_ "WHEAT"; int_ 1 ]) ]))
;;

(* ------------------------------------------------------------------ *)
(* Evaluation semantics *)
(* ------------------------------------------------------------------ *)

(* Everything below is a claim the golden vectors would also catch, but only if the family
   happens to contain the construct. These pin the ones monocrop-reorder-v1 exercises
   thinly or not at all. *)

let observation = Expr.SM.of_list [ "day", Expr.Vint 7; "wet", Expr.Vbool true ]
let vocabulary_kinds = observations

let vocabulary =
  Interpreter.vocabulary
    ~kinds:(Expr.SM.bindings vocabulary_kinds)
    ~accessors:
      (List.map
         (fun (name, _) ->
           name, fun obs (_ : Expr.value Expr.SM.t) -> Expr.SM.find name obs)
         (Expr.SM.bindings vocabulary_kinds))
    ()
;;

let run name json ~expect_action ~expect_registers =
  match load json with
  | exception exn -> report false name (Expr.error_message exn)
  | family ->
    let interpreter =
      Interpreter.create
        ~family
        ~parameters:(Family.bind family (`Assoc [ "crop", `String "WHEAT" ]))
        ~vocabulary
        ~build_action:(fun (turn : Interpreter.turn) ->
          let firing (f : Cascade.firing) =
            f.fired_op
            ^ String.concat
                ""
                (List.map (fun v -> "/" ^ Expr.value_name v) f.fired_operands)
          in
          String.concat
            "+"
            ((match turn.farmer with
              | None -> "-"
              | Some f -> firing f)
             :: List.map firing turn.market))
    in
    let action, registers =
      Interpreter.step interpreter observation (Interpreter.initial_registers interpreter)
    in
    let got =
      List.map
        (fun (name, value) -> name, Expr.value_name value)
        (Expr.SM.bindings registers)
    in
    let want = List.sort compare expect_registers in
    if action = expect_action && got = want
    then report true name action
    else
      report
        false
        name
        (Printf.sprintf
           "action %s registers %s"
           action
           (String.concat " " (List.map (fun (n, v) -> Printf.sprintf "%s=%s" n v) got)))
;;

let () =
  (* First-match-wins in the farmer cascade; all-match in the market group. *)
  run
    "cascade disciplines"
    (with_key
       "farmer_cascade"
       (`List
         [ rule
             "never"
             (`List [ `String "const"; `Bool false ])
             (`List [ `String "PASS" ])
         ; rule
             "first"
             const_true
             (`List [ `String "PLANT"; `List [ `String "param"; `String "crop" ] ])
         ; rule "second" const_true (`List [ `String "PASS" ])
         ])
     |> fun json ->
     match json with
     | `Assoc fields ->
       `Assoc
         (List.map
            (fun (k, v) ->
              if k = "market_rules"
              then
                ( k
                , `List
                    [ rule
                        "sell_one"
                        const_true
                        (`List [ `String "SELL"; str_ "WHEAT"; int_ 1 ])
                    ; rule
                        "sell_two"
                        const_true
                        (`List [ `String "SELL"; str_ "WHEAT"; int_ 2 ])
                    ] )
              else k, v)
            fields)
     | _ -> assert false)
    ~expect_action:"PLANT/'WHEAT'+SELL/'WHEAT'/1+SELL/'WHEAT'/2"
    ~expect_registers:[ "counter", "0"; "colour", "'RED'" ];
  (* An empty cascade falls through to no firing, and [fired] reads as the empty string. *)
  run
    "no rule fires"
    (with_key
       "commit"
       (`List
         [ write
             "colour"
             (`List
               [ `String "if"
               ; `List
                   [ `String "=="; `List [ `String "fired"; `String "farmer" ]; str_ "" ]
               ; str_ "GREEN"
               ; str_ "RED"
               ])
         ]))
    ~expect_action:"-"
    ~expect_registers:[ "counter", "0"; "colour", "'GREEN'" ];
  (* Writes within a stage commit simultaneously: [state] still reads the start-of-stage
     value even after an earlier write in the same stage changed it. *)
  run
    "simultaneous commit"
    (with_key
       "observe"
       (`List
         [ write "counter" (int_ 5)
         ; write
             "colour"
             (`List
               [ `String "if"
               ; `List [ `String ">"; state "counter"; int_ 0 ]
               ; str_ "GREEN"
               ; str_ "RED"
               ])
         ]))
    ~expect_action:"-"
    ~expect_registers:[ "counter", "5"; "colour", "'RED'" ];
  (* ...and ["next", reg] is the deliberate exception that does see it. *)
  run
    "next sees the in-stage write"
    (with_key
       "observe"
       (`List
         [ write "counter" (int_ 5)
         ; write
             "colour"
             (`List
               [ `String "if"
               ; `List [ `String ">"; next "counter"; int_ 0 ]
               ; str_ "GREEN"
               ; str_ "RED"
               ])
         ]))
    ~expect_action:"-"
    ~expect_registers:[ "counter", "5"; "colour", "'GREEN'" ]
;;

(* Short-circuiting is load-bearing rather than an optimisation: a guard such as
   harvest_ready relies on [and] to keep an accessor from being asked for a field the tile
   does not have. An accessor that raises when reached proves the guard never reaches it. *)
let () =
  let reached = ref false in
  let vocabulary =
    Interpreter.vocabulary
      ~kinds:[ "day", Expr.int_kind; "wet", Expr.bool_kind ]
      ~accessors:
        [ ("day", fun _ _ -> Expr.Vint 7)
        ; ( "wet"
          , fun _ _ ->
              reached := true;
              Expr.Vbool true )
        ]
      ()
  in
  let json =
    with_key
      "reset_when"
      (`List [ `String "and"; `List [ `String ">"; obs "day"; int_ 100 ]; obs "wet" ])
  in
  match load json with
  | exception exn -> report false "short-circuit" (Expr.error_message exn)
  | family ->
    let interpreter =
      Interpreter.create
        ~family
        ~parameters:(Family.bind family (`Assoc [ "crop", `String "WHEAT" ]))
        ~vocabulary
        ~build_action:(fun (_ : Interpreter.turn) -> ())
    in
    let (), _ =
      Interpreter.step interpreter observation (Interpreter.initial_registers interpreter)
    in
    report (not !reached) "short-circuit" "the second operand was never evaluated"
;;

(* ------------------------------------------------------------------ *)
(* Candidate binding *)
(* ------------------------------------------------------------------ *)

let () =
  let bounded =
    with_key
      "parameters"
      (`Assoc
        [ "crop", `Assoc [ "type", `String "enum"; "values", `List [ `String "WHEAT" ] ]
        ; "batch", `Assoc [ "type", `String "int"; "min", `Int 1; "max", `Int 8 ]
        ])
  in
  let family = load bounded in
  let expect_bind name json ok =
    match Family.bind family json with
    | (_ : Expr.value Expr.SM.t) ->
      report ok name (if ok then "binds" else "binding accepted a bad candidate")
    | exception exn -> report (not ok) name (Expr.error_message exn)
  in
  expect_bind
    "a valid candidate"
    (`Assoc [ "crop", `String "WHEAT"; "batch", `Int 4 ])
    true;
  expect_bind "a candidate missing a parameter" (`Assoc [ "crop", `String "WHEAT" ]) false;
  expect_bind
    "a candidate with an unknown parameter"
    (`Assoc [ "crop", `String "WHEAT"; "batch", `Int 4; "extra", `Int 1 ])
    false;
  expect_bind
    "a parameter below its minimum"
    (`Assoc [ "crop", `String "WHEAT"; "batch", `Int 0 ])
    false;
  expect_bind
    "a parameter above its maximum"
    (`Assoc [ "crop", `String "WHEAT"; "batch", `Int 9 ])
    false;
  expect_bind
    "a parameter outside its enum domain"
    (`Assoc [ "crop", `String "MELON"; "batch", `Int 4 ])
    false;
  expect_bind
    "a parameter of the wrong kind"
    (`Assoc [ "crop", `String "WHEAT"; "batch", `String "4" ])
    false
;;

(* The vocabulary seam checks its own consistency, and the interpreter checks that every
   accessor the family reads has the parameters it depends on. *)
let () =
  (match
     Interpreter.vocabulary
       ~kinds:[ "day", Expr.int_kind; "wet", Expr.bool_kind ]
       ~accessors:[ ("day", fun _ _ -> Expr.Vint 0) ]
       ()
   with
   | (_ : _ Interpreter.vocabulary) ->
     report false "vocabulary with an unimplemented accessor" "accepted"
   | exception exn ->
     report true "vocabulary with an unimplemented accessor" (Expr.error_message exn));
  let needy =
    Interpreter.vocabulary
      ~kinds:[ "day", Expr.int_kind; "wet", Expr.bool_kind ]
      ~accessors:[ ("day", fun _ _ -> Expr.Vint 0); ("wet", fun _ _ -> Expr.Vbool false) ]
      ~requires:[ "day", [ "calendar" ] ]
      ()
  in
  let family = load (with_key "reset_when" (`List [ `String ">"; obs "day"; int_ 0 ])) in
  match
    Interpreter.create
      ~family
      ~parameters:(Family.bind family (`Assoc [ "crop", `String "WHEAT" ]))
      ~vocabulary:needy
      ~build_action:(fun (_ : Interpreter.turn) -> ())
  with
  | (_ : (_, unit) Interpreter.t) ->
    report false "an accessor needing an undeclared parameter" "accepted"
  | exception exn ->
    report true "an accessor needing an undeclared parameter" (Expr.error_message exn)
;;

let () = if !failures > 0 then exit 1
