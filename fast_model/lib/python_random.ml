(* CPython-exact random.Random: MT19937 plus the exact draw methods upstream uses.

   The pinned kaggriculture.py derives a fresh generator each simulated day —
   [random.Random ((seed * 1_000_003) lxor day)] — and draws only
   [rng.random ()] (weed spawns) and [rng.choice] (shop unlocks). Exact
   differential replay therefore needs CPython's integer seeding
   (init_by_array over the 32-bit little-endian words of [abs n]), its
   [random ()] (the 53-bit genrand_res53 construction), and its
   [choice]/[_randbelow] (rejection sampling over [getrandbits]).

   Everything is masked to 32 bits explicitly; OCaml's 63-bit native int holds
   every intermediate product (largest is 32-bit x 32-bit = 62 bits).
   test/python_random_test.ml proves the outputs against draws recorded from
   CPython itself. *)

let n = 624
let m = 397
let matrix_a = 0x9908b0df
let upper_mask = 0x80000000
let lower_mask = 0x7fffffff
let mask32 = 0xffffffff

type t =
  { mt : int array
  ; mutable mti : int
  }

let init_genrand seed =
  let mt = Array.make n 0 in
  mt.(0) <- seed land mask32;
  for i = 1 to n - 1 do
    mt.(i) <- (1812433253 * (mt.(i - 1) lxor (mt.(i - 1) lsr 30)) + i) land mask32
  done;
  { mt; mti = n }

let init_by_array key =
  let state = init_genrand 19650218 in
  let mt = state.mt in
  let key_length = Array.length key in
  let i = ref 1
  and j = ref 0 in
  for _ = 1 to max n key_length do
    mt.(!i)
    <- (mt.(!i) lxor ((mt.(!i - 1) lxor (mt.(!i - 1) lsr 30)) * 1664525))
       + key.(!j) + !j;
    mt.(!i) <- mt.(!i) land mask32;
    incr i;
    incr j;
    if !i >= n then (
      mt.(0) <- mt.(n - 1);
      i := 1);
    if !j >= key_length then j := 0
  done;
  for _ = 1 to n - 1 do
    mt.(!i)
    <- (mt.(!i) lxor ((mt.(!i - 1) lxor (mt.(!i - 1) lsr 30)) * 1566083941)) - !i;
    mt.(!i) <- mt.(!i) land mask32;
    incr i;
    if !i >= n then (
      mt.(0) <- mt.(n - 1);
      i := 1)
  done;
  mt.(0) <- upper_mask;
  state

(* CPython random_seed for an int argument: split [abs n] into 32-bit words,
   little-endian, and init_by_array over them; zero becomes the single word 0. *)
let create seed =
  let seed = abs seed in
  let words = ref [] in
  let rest = ref seed in
  while !rest > 0 do
    words := (!rest land mask32) :: !words;
    rest := !rest lsr 32
  done;
  let key =
    match List.rev !words with
    | [] -> [| 0 |]
    | words -> Array.of_list words
  in
  init_by_array key

let genrand_uint32 state =
  if state.mti >= n then (
    let mt = state.mt in
    for kk = 0 to n - m - 1 do
      let y = mt.(kk) land upper_mask lor (mt.(kk + 1) land lower_mask) in
      mt.(kk) <- mt.(kk + m) lxor (y lsr 1) lxor (if y land 1 = 1 then matrix_a else 0)
    done;
    for kk = n - m to n - 2 do
      let y = mt.(kk) land upper_mask lor (mt.(kk + 1) land lower_mask) in
      mt.(kk)
      <- mt.(kk + (m - n)) lxor (y lsr 1) lxor (if y land 1 = 1 then matrix_a else 0)
    done;
    let y = mt.(n - 1) land upper_mask lor (mt.(0) land lower_mask) in
    mt.(n - 1) <- mt.(m - 1) lxor (y lsr 1) lxor (if y land 1 = 1 then matrix_a else 0);
    state.mti <- 0);
  let y = state.mt.(state.mti) in
  state.mti <- state.mti + 1;
  let y = y lxor (y lsr 11) in
  let y = y lxor ((y lsl 7) land 0x9d2c5680) in
  let y = y lxor ((y lsl 15) land 0xefc60000) in
  (y lxor (y lsr 18)) land mask32

(* CPython random_random: genrand_res53. *)
let random state =
  let a = genrand_uint32 state lsr 5 in
  let b = genrand_uint32 state lsr 6 in
  float_of_int ((a * 67108864) + b) *. (1.0 /. 9007199254740992.0)

(* CPython getrandbits for k <= 32: the top k bits of one word. Upstream's only
   consumer is _randbelow over small collections, so wider draws are refused
   rather than half-implemented. *)
let getrandbits state k =
  if k <= 0 || k > 32 then invalid_arg "getrandbits: k must be in 1..32";
  genrand_uint32 state lsr (32 - k)

let bit_length value =
  let rec loop value bits = if value = 0 then bits else loop (value lsr 1) (bits + 1) in
  loop value 0

(* CPython _randbelow_with_getrandbits: rejection sampling. *)
let randbelow state bound =
  if bound <= 0 then invalid_arg "randbelow: bound must be positive";
  let k = bit_length bound in
  let rec draw () =
    let r = getrandbits state k in
    if r >= bound then draw () else r
  in
  draw ()

(* CPython choice: index by _randbelow. *)
let choice state values =
  if Array.length values = 0 then invalid_arg "choice: empty";
  values.(randbelow state (Array.length values))
