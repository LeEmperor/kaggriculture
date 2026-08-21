(* The DSL v1 expression language as typed OCaml values.

   This is the authoring analogue of submission/dsl/expr.py, per Decision 4 of
   docs/ocaml_migration_decisions.md: OCaml elaborates, JSON is emitted, and the frozen
   Python interpreter runs it. The GADT makes the checks family.load performs at load time
   into checks the compiler performs at build time — operator arity and kind agreement
   cannot be written down wrongly here.

   What the types deliberately do not carry: enum value domains, the stage restrictions on
   [next]/[fired], and the requirement that every referenced parameter and register was
   declared. Those are validated by Family.create, and the Python loader remains the final
   gate on the emitted artifact. *)

type ikind = Ikind_witness
type bkind = Bkind_witness
type skind = Skind_witness

type _ kind =
  | Int : ikind kind
  | Bool : bkind kind
  | Enum : string list -> skind kind

type cls =
  | Decision
  | Telemetry

type 'k param =
  { p_name : string
  ; p_kind : 'k kind
  ; p_min : int option
  ; p_max : int option
  }

type _ value =
  | Vint : int -> ikind value
  | Vbool : bool -> bkind value
  | Vstr : string -> skind value

type 'k reg =
  { r_name : string
  ; r_kind : 'k kind
  ; r_init : 'k value
  ; r_cls : cls
  }

type 'k obs =
  { o_name : string
  ; o_kind : 'k kind
  }

type group =
  | Farmer
  | Market

let group_name = function
  | Farmer -> "farmer"
  | Market -> "market"
;;

type arith =
  | Add
  | Sub
  | Mul
  | Minimum
  | Maximum

type cmp =
  | Lt
  | Le
  | Gt
  | Ge

type eq =
  | Equal
  | Not_equal

type _ t =
  | Const : 'k value -> 'k t
  | Param : 'k param -> 'k t
  | State : 'k reg -> 'k t
  | Next : 'k reg -> 'k t
  | Obs : 'k obs -> 'k t
  | Fired : group -> skind t
  | Fired_any : group * string -> bkind t
  | Arith : arith * ikind t * ikind t -> ikind t
  | Cmp : cmp * ikind t * ikind t -> bkind t
  | Eq : eq * 'k t * 'k t -> bkind t
  | And : bkind t list -> bkind t
  | Or : bkind t list -> bkind t
  | Not : bkind t -> bkind t
  | If : bkind t * 'k t * 'k t -> 'k t

module Param = struct
  let int ?min ?max name =
    (match min, max with
     | Some low, Some high when low > high ->
       invalid_arg (Printf.sprintf "parameter %s: min %d exceeds max %d" name low high)
     | _ -> ());
    { p_name = name; p_kind = Int; p_min = min; p_max = max }
  ;;

  let enum name ~values =
    if values = [] then invalid_arg (Printf.sprintf "parameter %s: empty enum" name);
    { p_name = name; p_kind = Enum values; p_min = None; p_max = None }
  ;;
end

module Reg = struct
  let int name ~init ~cls =
    { r_name = name; r_kind = Int; r_init = Vint init; r_cls = cls }
  ;;

  let bool name ~init ~cls =
    { r_name = name; r_kind = Bool; r_init = Vbool init; r_cls = cls }
  ;;

  let enum name ~values ~init ~cls =
    if not (List.mem init values)
    then
      invalid_arg
        (Printf.sprintf "register %s: init '%s' is outside its declared domain" name init);
    { r_name = name; r_kind = Enum values; r_init = Vstr init; r_cls = cls }
  ;;
end

module Obs = struct
  let int name = { o_name = name; o_kind = Int }
  let bool name = { o_name = name; o_kind = Bool }
end

(* Smart constructors, Hardcaml-style: open [Expr.O] locally where a family is authored.
   Colon-suffixed operators keep OCaml's arithmetic and comparison precedences, so guards
   read the way they will emit. *)
module O = struct
  let int n = Const (Vint n)
  let bool b = Const (Vbool b)
  let str s = Const (Vstr s)
  let param p = Param p
  let state r = State r
  let next r = Next r
  let obs o = Obs o
  let fired g = Fired g
  let fired_any g rule = Fired_any (g, rule)
  let ( +: ) a b = Arith (Add, a, b)
  let ( -: ) a b = Arith (Sub, a, b)
  let ( *: ) a b = Arith (Mul, a, b)
  let min_ a b = Arith (Minimum, a, b)
  let max_ a b = Arith (Maximum, a, b)
  let ( <: ) a b = Cmp (Lt, a, b)
  let ( <=: ) a b = Cmp (Le, a, b)
  let ( >: ) a b = Cmp (Gt, a, b)
  let ( >=: ) a b = Cmp (Ge, a, b)
  let ( ==: ) a b = Eq (Equal, a, b)
  let ( <>: ) a b = Eq (Not_equal, a, b)
  let and_ es = And es
  let or_ es = Or es
  let not_ e = Not e
  let if_ c t e = If (c, t, e)
end

let arith_name = function
  | Add -> "+"
  | Sub -> "-"
  | Mul -> "*"
  | Minimum -> "min"
  | Maximum -> "max"
;;

let cmp_name = function
  | Lt -> "<"
  | Le -> "<="
  | Gt -> ">"
  | Ge -> ">="
;;

let eq_name = function
  | Equal -> "=="
  | Not_equal -> "!="
;;

let value_json : type k. k value -> Yojson.Safe.t = function
  | Vint n -> `Int n
  | Vbool b -> `Bool b
  | Vstr s -> `String s
;;

let rec to_json : type k. k t -> Yojson.Safe.t = function
  | Const v -> `List [ `String "const"; value_json v ]
  | Param p -> `List [ `String "param"; `String p.p_name ]
  | State r -> `List [ `String "state"; `String r.r_name ]
  | Next r -> `List [ `String "next"; `String r.r_name ]
  | Obs o -> `List [ `String "obs"; `String o.o_name ]
  | Fired g -> `List [ `String "fired"; `String (group_name g) ]
  | Fired_any (g, rule) ->
    `List [ `String "fired?"; `String (group_name g); `String rule ]
  | Arith (op, a, b) -> `List [ `String (arith_name op); to_json a; to_json b ]
  | Cmp (op, a, b) -> `List [ `String (cmp_name op); to_json a; to_json b ]
  | Eq (op, a, b) -> `List [ `String (eq_name op); to_json a; to_json b ]
  | And es -> `List (`String "and" :: List.map to_json es)
  | Or es -> `List (`String "or" :: List.map to_json es)
  | Not e -> `List [ `String "not"; to_json e ]
  | If (c, t, e) -> `List [ `String "if"; to_json c; to_json t; to_json e ]
;;

(* The string values an skind expression can evaluate to, when that is statically knowable
   — the authoring-side mirror of the Python loader's [Kind.values] inference, used by
   Family.create to check writes against a register's declared enum domain. [None] means
   unbounded, and matches the loader's behaviour of only checking when both sides carry a
   domain. *)
let rec possible_strings : skind t -> string list option = function
  | Const (Vstr s) -> Some [ s ]
  | Param { p_kind = Enum values; _ } -> Some values
  | State { r_kind = Enum values; _ } -> Some values
  | Next { r_kind = Enum values; _ } -> Some values
  | Obs { o_kind = Enum values; _ } -> Some values
  | Fired _ -> None
  | If (_, a, b) ->
    (match possible_strings a, possible_strings b with
     | Some xs, Some ys -> Some (xs @ ys)
     | _ -> None)
;;
