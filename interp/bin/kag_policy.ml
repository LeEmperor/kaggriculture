(* kag-policy: the subprocess policy shim.

   kag_policy.exe --family FILE --candidate FILE [--policy-id ID]

   Step 5 of the work plan in docs/ocaml_migration_decisions.md, and the reason Decision 1
   ("no FFI, process boundary only") costs nothing. One JSON request per line on stdin, one
   JSON response per line on stdout:

     {"op":"ping"}                             -> {"ok":true,"policy_id":...,"registers":...}
     {"op":"reset"}                            -> {"ok":true,"registers":...}
     {"observation":OBS}                       -> {"action":...,"registers":...}
     {"observation":OBS,"registers":REGS}      -> {"action":...,"registers":...}

   The two step forms are the two jobs this binary has. With a register bank supplied the
   step is stateless, which is what replaying a golden vector needs — each vector carries
   its own previous_policy_state and nothing may leak between them. Without one the process
   keeps a bank of its own, which is what driving a live episode through
   reference/run_game.py needs. Both answer with the resulting bank, so the caller can
   compare registers without a second round trip.

   A malformed or ill-typed request answers {"error": ...} and the loop continues: one bad
   vector should report itself, not take the process down. Anything that goes wrong before
   the loop starts — an unreadable family, a candidate that fails validation — exits
   nonzero, because there is nothing meaningful left to serve. *)

let usage () =
  prerr_endline "usage: kag_policy.exe --family FILE --candidate FILE [--policy-id ID]";
  exit 2
;;

type options =
  { family_path : string
  ; candidate_path : string
  ; policy_id : string option
  }

let parse_argv argv =
  let family_path = ref ""
  and candidate_path = ref ""
  and policy_id = ref None in
  let i = ref 1 in
  let argc = Array.length argv in
  while !i < argc do
    (match argv.(!i) with
     | "--family" when !i + 1 < argc ->
       incr i;
       family_path := argv.(!i)
     | "--candidate" when !i + 1 < argc ->
       incr i;
       candidate_path := argv.(!i)
     | "--policy-id" when !i + 1 < argc ->
       incr i;
       policy_id := Some argv.(!i)
     | "--help" | "-h" -> usage ()
     | arg ->
       Printf.eprintf "unknown argument: %s\n" arg;
       usage ());
    incr i
  done;
  if !family_path = "" || !candidate_path = "" then usage ();
  { family_path = !family_path; candidate_path = !candidate_path; policy_id = !policy_id }
;;

let read_json path =
  match Yojson.Safe.from_file path with
  | json -> json
  | exception exn ->
    Printf.eprintf "cannot read %s: %s\n" path (Printexc.to_string exn);
    exit 2
;;

let field key json =
  match json with
  | `Assoc fields -> List.assoc_opt key fields
  | _ -> None
;;

let string_field key json =
  match field key json with
  | Some (`String value) -> Some value
  | _ -> None
;;

(* The candidate envelope check dsl_policy.py performs before constructing an interpreter.
   Doing it here too keeps the shim usable on its own rather than only behind that adapter. *)
let candidate_parameters ~family ~candidate ~expected_policy_id =
  let declared = string_field "policy_id" candidate in
  let expected =
    match expected_policy_id with
    | Some id -> id
    | None -> family.Policy_dsl.Family.policy_id
  in
  (match declared with
   | Some id when id = expected -> ()
   | Some id ->
     Printf.eprintf "candidate targets '%s' but the family is '%s'\n" id expected;
     exit 2
   | None ->
     prerr_endline "candidate has no 'policy_id'";
     exit 2);
  match field "parameters" candidate with
  | Some parameters -> parameters
  | None ->
    prerr_endline "candidate has no 'parameters' block";
    exit 2
;;

let respond json =
  print_string (Yojson.Safe.to_string json);
  print_newline ();
  flush stdout
;;

let () =
  let options = parse_argv Sys.argv in
  let family =
    match
      Policy_dsl.Family.load
        (read_json options.family_path)
        ~observations:Kag_vocabulary.Vocabulary.t.Policy_dsl.Interpreter.kinds
        ~emits:Kag_vocabulary.Actions.emits
    with
    | family -> family
    | exception exn ->
      Printf.eprintf "%s: %s\n" options.family_path (Policy_dsl.Expr.error_message exn);
      exit 2
  in
  let candidate = read_json options.candidate_path in
  let parameters =
    candidate_parameters ~family ~candidate ~expected_policy_id:options.policy_id
  in
  let interpreter =
    match
      Policy_dsl.Interpreter.create
        ~family
        ~parameters:(Policy_dsl.Family.bind family parameters)
        ~vocabulary:Kag_vocabulary.Vocabulary.t
        ~build_action:Kag_vocabulary.Actions.build_action
    with
    | interpreter -> interpreter
    | exception exn ->
      Printf.eprintf "%s: %s\n" options.candidate_path (Policy_dsl.Expr.error_message exn);
      exit 2
  in
  let held = ref (Policy_dsl.Interpreter.initial_registers interpreter) in
  let handle request =
    match string_field "op" request, field "observation" request with
    | Some "ping", _ ->
      `Assoc
        [ "ok", `Bool true
        ; "policy_id", `String family.Policy_dsl.Family.policy_id
        ; "family", `String family.Policy_dsl.Family.family_name
        ; "family_version", `Int family.Policy_dsl.Family.family_version
        ; "dsl_version", `Int family.Policy_dsl.Family.encoding_version
        ; "registers", Policy_dsl.Family.registers_to_json family !held
        ]
    | Some "reset", _ ->
      held := Policy_dsl.Interpreter.initial_registers interpreter;
      `Assoc
        [ "ok", `Bool true
        ; "registers", Policy_dsl.Family.registers_to_json family !held
        ]
    | (None | Some "step"), Some observation ->
      let stateless = field "registers" request in
      let incoming =
        match stateless with
        | Some json -> Policy_dsl.Family.registers_of_json family json
        | None -> !held
      in
      let action, registers =
        Policy_dsl.Interpreter.step interpreter observation incoming
      in
      if stateless = None then held := registers;
      `Assoc
        [ "action", action
        ; "registers", Policy_dsl.Family.registers_to_json family registers
        ]
    | Some op, _ -> `Assoc [ "error", `String (Printf.sprintf "unknown op '%s'" op) ]
    | None, None -> `Assoc [ "error", `String "request has no 'observation'" ]
  in
  try
    while true do
      let line = input_line stdin in
      if String.trim line <> ""
      then
        respond
          (match handle (Yojson.Safe.from_string line) with
           | response -> response
           | exception exn ->
             `Assoc [ "error", `String (Policy_dsl.Expr.error_message exn) ])
    done
  with
  | End_of_file -> ()
;;
