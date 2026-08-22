(* A deterministic PRNG for the baselines, deliberately *not* Python_random.

   Two reasons it is separate. The model's generator reproduces CPython bit-for-bit
   because the environment's own randomness is part of the transition, and a policy
   drawing from it would be indistinguishable from a differential failure. And a
   policy's randomness must be explicitly seeded and independent of environment
   randomness (CLAUDE.md, Conventions) — so this is seeded from the game seed and the
   seat, and reproduces exactly under replay.

   SplitMix64: one multiply-xor-shift chain, no state beyond a counter. *)

type t = { mutable state : int64 }

let mix64 z =
  let open Int64 in
  let z = mul (logxor z (shift_right_logical z 30)) 0xbf58476d1ce4e5b9L in
  let z = mul (logxor z (shift_right_logical z 27)) 0x94d049bb133111ebL in
  logxor z (shift_right_logical z 31)
;;

(* Distinct label/seed/seat triples give unrelated streams: the label is folded in as a
   string hash so adding a baseline never shifts an existing one's draws. *)
let create ~label ~seed ~seat =
  let hash = ref 0xcbf29ce484222325L in
  String.iter
    (fun c ->
      hash
      := Int64.mul
           (Int64.logxor !hash (Int64.of_int (Char.code c)))
           0x100000001b3L)
    label;
  let mixed =
    mix64
      (Int64.logxor
         (Int64.add !hash (Int64.of_int (seed * 1_000_003)))
         (Int64.of_int (seat + 1)))
  in
  { state = mixed }
;;

let next t =
  t.state <- Int64.add t.state 0x9e3779b97f4a7c15L;
  mix64 t.state
;;

(* Uniform on [0, bound). Rejection-free modulo bias is not worth the code here: bounds
   are tiny (action-menu sizes) against a 62-bit draw. *)
let below t bound =
  if bound <= 0
  then 0
  else Int64.to_int (Int64.rem (Int64.shift_right_logical (next t) 2) (Int64.of_int bound))
;;

let chance t ~numerator ~denominator = below t denominator < numerator
let pick t array = array.(below t (Array.length array))
