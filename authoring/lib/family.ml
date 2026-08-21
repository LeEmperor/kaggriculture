(* The family container and its residual validation, then JSON emission.

   Family.create checks exactly what the GADT in Expr cannot: that every referenced
   parameter and register was declared to this family, the stage restrictions on [next]
   and [fired] (the same rules submission/dsl/family.py enforces at load), no double write
   within a stage, rule-name uniqueness, and enum writes staying inside a register's
   declared domain. A family value that exists can therefore be emitted, and the emitted
   JSON is expected to pass the Python loader unchanged — that loader stays the final
   gate. *)

module SS = Set.Make (String)

let dsl_version = 1

type packed_param = P : 'k Expr.param -> packed_param
type packed_reg = R : 'k Expr.reg -> packed_reg
type write = W : 'k Expr.reg * 'k Expr.t -> write
type operand = O : 'k Expr.t -> operand

type emit =
  { op : string
  ; operands : operand list
  }

type rule =
  { rule_name : string
  ; when_ : Expr.bkind Expr.t
  ; emit : emit
  }

type t =
  { policy_id : string
  ; family : string
  ; family_version : int
  ; parameters : packed_param list
  ; registers : packed_reg list
  ; reset_when : Expr.bkind Expr.t
  ; observe : write list
  ; market_rules : rule list
  ; farmer_cascade : rule list
  ; commit : write list
  }

let write : type k. k Expr.reg -> k Expr.t -> write = fun reg value -> W (reg, value)
let emit op operands = { op; operands }
let rule ~name ~when_ ~emit = { rule_name = name; when_; emit }

(* ------------------------------------------------------------------ *)
(* Validation *)
(* ------------------------------------------------------------------ *)

type ctx =
  { params : SS.t
  ; regs : SS.t
  ; next_ok : SS.t option (* None: illegal here; Some s: registers written so far *)
  ; fired_ok : bool
  ; farmer_names : SS.t
  ; market_names : SS.t
  ; where : string
  }

let fail ctx message = failwith (Printf.sprintf "%s: %s" ctx.where message)

let rec check : type k. ctx -> k Expr.t -> unit =
  fun ctx expr ->
  match expr with
  | Expr.Const _ -> ()
  | Expr.Param p ->
    if not (SS.mem p.Expr.p_name ctx.params)
    then
      fail
        ctx
        (Printf.sprintf "parameter '%s' is not declared in this family" p.Expr.p_name)
  | Expr.State r ->
    if not (SS.mem r.Expr.r_name ctx.regs)
    then
      fail
        ctx
        (Printf.sprintf "register '%s' is not declared in this family" r.Expr.r_name)
  | Expr.Next r ->
    (match ctx.next_ok with
     | None -> fail ctx "['next', ...] is only legal in the observe and commit stages"
     | Some written ->
       if not (SS.mem r.Expr.r_name written)
       then
         fail
           ctx
           (Printf.sprintf
              "['next', '%s'] reads a register not yet written in this stage"
              r.Expr.r_name))
  | Expr.Obs _ -> ()
  | Expr.Fired _ ->
    if not ctx.fired_ok then fail ctx "['fired', ...] is legal only in the commit stage"
  | Expr.Fired_any (group, name) ->
    if not ctx.fired_ok
    then fail ctx "['fired?', ...] is legal only in the commit stage"
    else (
      let names =
        match group with
        | Expr.Farmer -> ctx.farmer_names
        | Expr.Market -> ctx.market_names
      in
      if not (SS.mem name names)
      then
        fail
          ctx
          (Printf.sprintf
             "['fired?', '%s', '%s'] names a rule that does not exist"
             (Expr.group_name group)
             name))
  | Expr.Arith (_, a, b) ->
    check ctx a;
    check ctx b
  | Expr.Cmp (_, a, b) ->
    check ctx a;
    check ctx b
  | Expr.Eq (_, a, b) ->
    check ctx a;
    check ctx b
  | Expr.And es -> List.iter (check ctx) es
  | Expr.Or es -> List.iter (check ctx) es
  | Expr.Not e -> check ctx e
  | Expr.If (c, t, e) ->
    check ctx c;
    check ctx t;
    check ctx e
;;

let check_domain : type k. ctx -> k Expr.reg -> k Expr.t -> unit =
  fun ctx reg value ->
  match reg.Expr.r_kind with
  | Expr.Int -> ()
  | Expr.Bool -> ()
  | Expr.Enum domain ->
    (match Expr.possible_strings value with
     | None -> ()
     | Some values ->
       List.iter
         (fun v ->
           if not (List.mem v domain)
           then
             fail
               ctx
               (Printf.sprintf
                  "writes value '%s' outside the declared domain of '%s'"
                  v
                  reg.Expr.r_name))
         values)
;;

let check_writes ctx stage writes =
  let (_ : SS.t) =
    List.fold_left
      (fun written (W (reg, value)) ->
        let name = reg.Expr.r_name in
        let ctx = { ctx with next_ok = Some written; where = stage ^ "." ^ name } in
        if not (SS.mem name ctx.regs)
        then fail ctx (Printf.sprintf "register '%s' is not declared in this family" name);
        if SS.mem name written
        then
          fail
            ctx
            (Printf.sprintf
               "'%s' is written twice in the %s stage; writes commit simultaneously"
               name
               stage);
        check ctx value;
        check_domain ctx reg value;
        SS.add name written)
      SS.empty
      writes
  in
  ()
;;

let check_rules ctx stage rules =
  let (_ : SS.t) =
    List.fold_left
      (fun seen r ->
        let ctx = { ctx with where = stage ^ "." ^ r.rule_name } in
        if r.rule_name = "" then fail ctx "a rule needs a non-empty name";
        if SS.mem r.rule_name seen
        then fail ctx (Printf.sprintf "duplicate rule name '%s'" r.rule_name);
        check ctx r.when_;
        List.iter (fun (O operand) -> check ctx operand) r.emit.operands;
        SS.add r.rule_name seen)
      SS.empty
      rules
  in
  ()
;;

let unique_names kind names =
  let (_ : SS.t) =
    List.fold_left
      (fun seen name ->
        if SS.mem name seen
        then failwith (Printf.sprintf "duplicate %s name '%s'" kind name);
        SS.add name seen)
      SS.empty
      names
  in
  ()
;;

let create
  ~policy_id
  ~family
  ~family_version
  ~parameters
  ~registers
  ~reset_when
  ~observe
  ~market_rules
  ~farmer_cascade
  ~commit
  =
  (* register represent internal memory of the strategy *)
  if registers = [] then failwith "a family needs at least one register";
  let param_names = List.map (fun (P p) -> p.Expr.p_name) parameters in
  let reg_names = List.map (fun (R r) -> r.Expr.r_name) registers in
  unique_names "parameter" param_names;
  unique_names "register" reg_names;
  let base =
    { params = SS.of_list param_names
    ; regs = SS.of_list reg_names
    ; next_ok = None
    ; fired_ok = false
    ; farmer_names = SS.of_list (List.map (fun r -> r.rule_name) farmer_cascade)
    ; market_names = SS.of_list (List.map (fun r -> r.rule_name) market_rules)
    ; where = ""
    }
  in
  check { base with where = "reset_when" } reset_when;
  check_writes base "observe" observe;
  check_rules base "market_rules" market_rules;
  check_rules base "farmer_cascade" farmer_cascade;
  check_writes { base with fired_ok = true } "commit" commit;
  { policy_id
  ; family
  ; family_version
  ; parameters
  ; registers
  ; reset_when
  ; observe
  ; market_rules
  ; farmer_cascade
  ; commit
  }
;;

(* ------------------------------------------------------------------ *)
(* Emission *)
(* ------------------------------------------------------------------ *)

let param_json (P p) : string * Yojson.Safe.t =
  let fields =
    match p.Expr.p_kind with
    | Expr.Int ->
      [ "type", `String "int" ]
      @ (match p.Expr.p_min with
         | None -> []
         | Some low -> [ "min", `Int low ])
      @
        (match p.Expr.p_max with
        | None -> []
        | Some high -> [ "max", `Int high ])
    | Expr.Enum values ->
      [ "type", `String "enum"; "values", `List (List.map (fun v -> `String v) values) ]
    | Expr.Bool -> failwith "boolean parameters are not supported"
  in
  p.Expr.p_name, `Assoc fields
;;

let register_json (R r) : string * Yojson.Safe.t =
  let type_fields =
    match r.Expr.r_kind with
    | Expr.Int -> [ "type", `String "int" ]
    | Expr.Bool -> [ "type", `String "bool" ]
    | Expr.Enum values ->
      [ "type", `String "enum"; "values", `List (List.map (fun v -> `String v) values) ]
  in
  let cls =
    match r.Expr.r_cls with
    | Expr.Decision -> "decision"
    | Expr.Telemetry -> "telemetry"
  in
  ( r.Expr.r_name
  , `Assoc (type_fields @ [ "init", Expr.value_json r.Expr.r_init; "class", `String cls ])
  )
;;

let write_json (W (reg, value)) : Yojson.Safe.t =
  `Assoc [ "reg", `String reg.Expr.r_name; "value", Expr.to_json value ]
;;

let rule_json r : Yojson.Safe.t =
  `Assoc
    [ "name", `String r.rule_name
    ; "when", Expr.to_json r.when_
    ; ( "emit"
      , `List
          (`String r.emit.op
           :: List.map (fun (O operand) -> Expr.to_json operand) r.emit.operands) )
    ]
;;

let to_json family : Yojson.Safe.t =
  `Assoc
    [ "policy_id", `String family.policy_id
    ; "family", `String family.family
    ; "family_version", `Int family.family_version
    ; "dsl_version", `Int dsl_version
    ; "parameters", `Assoc (List.map param_json family.parameters)
    ; "registers", `Assoc (List.map register_json family.registers)
    ; "reset_when", Expr.to_json family.reset_when
    ; "observe", `List (List.map write_json family.observe)
    ; "market_rules", `List (List.map rule_json family.market_rules)
    ; "farmer_cascade", `List (List.map rule_json family.farmer_cascade)
    ; "commit", `List (List.map write_json family.commit)
    ]
;;
