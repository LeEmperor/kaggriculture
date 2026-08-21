;;; Directory Local Variables. See (info "(emacs) Directory Variables").

;; ocamlformat rewrites .ml files on save (see .ocamlformat, profile =
;; janestreet).  These settings only govern how OCaml indents while you type,
;; before that rewrite lands, so they are chosen to match the janestreet
;; profile's output and keep the cursor from jumping on save.
((nil . ((indent-tabs-mode . nil)
         (fill-column . 90)))
 (caml-mode . ((tab-width . 2)))
 (ocaml-ts-mode . ((tab-width . 2)))
 (tuareg-mode . ((tab-width . 2)
                 (tuareg-indent-align-with-first-arg . t)
                 (tuareg-match-patterns-aligned . t))))
