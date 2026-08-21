(* Emit a family's JSON encoding to stdout — the Emit.json of Decision 4.

   Usage: dune exec bin/emit.exe [FAMILY] With no argument, or "monocrop_reorder", emits
   monocrop-reorder-v1. *)

let families = [ ("monocrop_reorder", fun () -> Families.Monocrop_reorder.family) ]

let () =
  let name =
    match Sys.argv with
    | [| _ |] -> "monocrop_reorder"
    | [| _; name |] -> name
    | _ ->
      prerr_endline "usage: emit [FAMILY]";
      exit 2
  in
  match List.assoc_opt name families with
  | None ->
    Printf.eprintf
      "unknown family '%s'; available: %s\n"
      name
      (String.concat ", " (List.map fst families));
    exit 2
  | Some family ->
    print_endline
      (Yojson.Safe.pretty_to_string ~std:true (Policy_family.Family.to_json (family ())))
;;
