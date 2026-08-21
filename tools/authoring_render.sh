#!/usr/bin/env bash
#
# The authoring edit -> see-the-result loop, as one process with one exit code.
#
#   tools/authoring_render.sh [FAMILY] [SEED]
#
# Chains the three stages that separate an edit to authoring/families/FAMILY.ml
# from a number you can judge:
#
#   1. dune build            typecheck; the GADT catches arity/kind errors here
#   2. emit.exe > family.json  regenerate the artifact the Python side consumes
#   3. experiments.policy_report  run one oracle episode through the DSL interpreter
#
# A failure at any stage stops the chain and exits non-zero, so an Emacs
# compilation buffer shows either compiler errors or a report, never a stale
# mix of both. Stage 2 writes through a temporary file: redirecting straight
# into family.json would truncate a tracked, generated artifact before dune
# had a chance to fail.
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

family="${1:-monocrop_reorder}"
seed="${2:-1234}"

family_dir="experiments/policies/${family}"
family_json="${family_dir}/family.json"
trace="experiments/results/${family}-latest.jsonl"
history="experiments/results/${family}-history.log"

if [ ! -d "$family_dir" ]; then
  echo "authoring_render: no such policy family: ${family_dir}" >&2
  exit 2
fi

# The plot-side venv is preferred only because it is the interpreter the rest of
# the Emacs integration already uses; the report itself needs nothing beyond the
# standard library and this repository.
python="${KAG_PYTHON:-}"
if [ -z "$python" ]; then
  if [ -x "slow_model/.venv/bin/python" ]; then
    python="slow_model/.venv/bin/python"
  else
    python="python3"
  fi
fi

echo "== dune build =="
dune build --display quiet || exit $?

echo
echo "== emit ${family} =="
tmp="$(mktemp)" || exit 1
trap 'rm -f "$tmp"' EXIT
dune exec --display quiet --no-build authoring/bin/emit.exe -- "$family" > "$tmp" || exit $?

if cmp -s "$tmp" "$family_json"; then
  echo "${family_json} unchanged"
else
  mv "$tmp" "$family_json" || exit 1
  echo "${family_json} regenerated"
fi

echo
echo "== policy report (seed ${seed}) =="
mkdir -p experiments/results
report="$("$python" -m experiments.policy_report \
  --seed "$seed" \
  --policy-a "experiments.policies.${family}.dsl_policy" \
  --trace "$trace" 2>&1)"
status=$?
echo "$report"
[ "$status" -eq 0 ] || exit "$status"

# One rolling headline per run. This is what makes a single output window
# sufficient: the previous runs' results stay visible next to the current one,
# so a margin regression is legible without a second buffer to compare against.
headline="$(printf '%s' "$report" | sed -n 's/^result *//p')"
if [ -n "$headline" ]; then
  printf '%s  seed=%s  %s\n' "$(date '+%H:%M:%S')" "$seed" "$headline" >> "$history"
fi

if [ -s "$history" ]; then
  echo
  echo "== recent runs (oldest first) =="
  tail -n 8 "$history"
fi
