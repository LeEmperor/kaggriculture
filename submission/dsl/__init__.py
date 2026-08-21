"""A backend-agnostic interpreter for data-defined policy families.

Nothing in this package imports from elsewhere in the project, and nothing in it
mentions Kaggriculture. The four modules are the four roles named in
``docs/library_boundaries.md``:

``expr``       the expression language: AST, kind inference, evaluation
``cascade``    first-match-wins rule selection and all-match rule groups
``pipeline``   the staged register machine and its simultaneous-commit rule
``family``     the parsed, validated family artifact

``interpreter`` binds those to an injected observation vocabulary and action
builder. The per-game half of that binding lives in ``submission/vocabulary.py``
and ``submission/actions.py``, which are separable from this package by
deletion rather than by refactor.
"""
