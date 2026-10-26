#!/usr/bin/env bash
# Path: scripts/run_h.sh
# Purpose: Run, or continue, the H variant(s) over a frozen B or B+C parent, then export and grade them.
set -euo pipefail
cd "$(dirname "$0")/.."
grader="../bixbench78_grader/score.py"   # private sibling repository; this checkout never grades in-process
usage="Usage: scripts/run_h.sh B_TAG [VERSION] [C_LEVEL] [PARENT_SCORING_JSON]   (B_TAG as in analysis/<B_TAG>_generation.json)
VERSION is h_v61 (default), h_v7 or all. C_LEVEL is c_minimal (default), c_single, c_blind or none;
it names the parent: analysis/<B_TAG>_<C_LEVEL>_generation.json, or the B run itself for none.
Completed variants are reported and skipped, an interrupted variant continues where it stopped
(finished tasks are kept), and a variant not yet started runs from scratch. The parent export is
graded by the sibling grader only while no H variant of this parent has started (unless
PARENT_SCORING_JSON is given); afterwards that scoring is reused, because the started runs are bound to it.
FRESH=1 deletes the selected variants' runs and outputs first. FEEDBACK_JUDGE=surrogate grades the
in-run Yes/No with the grader's offline rule instead of DeepSeek."
b_tag="${1:?$usage}"
[[ "$b_tag" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "invalid B_TAG" >&2; exit 2; }
version="${2:-h_v61}"
c_level="${3:-c_minimal}"
all_versions=(h_v61 h_v7)
case "$version" in
  all) versions=("${all_versions[@]}") ;;
  h_v61|h_v7) versions=("$version") ;;
  *) echo "VERSION must be h_v61, h_v7 or all; got '$version'" >&2; exit 2 ;;
esac
case "$c_level" in
  none) parent_tag="$b_tag"; parent_condition=b; parent_name=b ;;
  c_minimal|c_single|c_blind) parent_tag="${b_tag}_${c_level}"; parent_condition=c; parent_name="$c_level" ;;
  *) echo "C_LEVEL must be c_minimal, c_single, c_blind or none; got '$c_level'" >&2; exit 2 ;;
esac
generation="analysis/${parent_tag}_generation.json"
[[ -f "$generation" ]] || { echo "no frozen parent generation at $generation (run run_b.sh / run_c.sh first)" >&2; exit 2; }

completed() { [[ -f "analysis/${parent_tag}_$1_generation.json" ]]; }
started() { completed "$1" || [[ -n "$(ls -A "runs/${parent_tag}_$1" 2>/dev/null)" ]]; }
export_and_grade() {
  local tag="${parent_tag}_$1" out="results/${b_tag}/${parent_name}_$1"
  [[ -f "${out}.jsonl" ]] || python scripts/export_predictions.py \
    --generation "analysis/${tag}_generation.json" --condition h \
    --output "${out}.jsonl" --provenance "${out}.provenance.json" --source-run "$tag"
  [[ -f "analysis/${tag}_scoring.json" ]] || python "$grader" \
    --predictions "${out}.jsonl" --provenance "${out}.provenance.json" --output "analysis/${tag}_scoring.json"
}

mkdir -p "logs/${b_tag}" "results/${b_tag}"
if [[ "${FRESH:-0}" == 1 ]]; then
  for v in "${versions[@]}"; do
    rm -rf "runs/${parent_tag}_${v}"
    rm -f "analysis/${parent_tag}_${v}_generation.json" "analysis/${parent_tag}_${v}_scoring.json" \
          "reports/${parent_tag}_${v^^}_GENERATION.md" "results/${b_tag}/${parent_name}_${v}.jsonl" \
          "results/${b_tag}/${parent_name}_${v}.provenance.json" "logs/${b_tag}/${parent_name}_${v}.log"
  done
fi

[[ -f "$grader" ]] || { echo "no grader at $grader (H needs it for in-run Yes/No and final scoring)" >&2; exit 2; }
pending=()
for v in "${versions[@]}"; do
  if completed "$v"; then
    export_and_grade "$v"
    echo "H ${v} over ${parent_tag} has already completed: analysis/${parent_tag}_${v}_generation.json, analysis/${parent_tag}_${v}_scoring.json"
  else
    pending+=("$v")
  fi
done
if [[ ${#pending[@]} -eq 0 ]]; then
  echo "Nothing to run: every requested H variant over ${parent_tag} has completed (FRESH=1 to redo)."
  exit 0
fi

any_started=0
for v in "${all_versions[@]}"; do started "$v" && any_started=1; done
if [[ $# -ge 4 ]]; then
  scoring="$4"
  [[ -f "$scoring" ]] || { echo "no parent scoring at $scoring" >&2; exit 2; }
else
  scoring="analysis/${parent_tag}_scoring.json"
  if (( any_started )); then
    [[ -f "$scoring" ]] || { echo "H has started for ${parent_tag} but $scoring is missing; rerun as FRESH=1 scripts/run_h.sh ${b_tag} all ${c_level}" >&2; exit 2; }
    echo "Reusing $scoring: H runs over ${parent_tag} already started against it."
  elif [[ ! -f "$scoring" ]]; then
    python scripts/export_predictions.py --generation "$generation" --condition "$parent_condition" \
      --output "results/${b_tag}/${parent_name}.jsonl" --provenance "results/${b_tag}/${parent_name}.provenance.json" \
      --source-run "$parent_tag"
    python "$grader" --predictions "results/${b_tag}/${parent_name}.jsonl" \
      --provenance "results/${b_tag}/${parent_name}.provenance.json" --output "$scoring"
  else
    echo "Using existing $scoring."
  fi
fi
# The registry is derived from the scoring; rebuild it only while nothing is bound to it.
(( any_started )) || rm -f "analysis/${parent_tag}_h_parent.json"

model_args=()
[[ -n "${MODEL:-}" ]] && model_args+=(--model "$MODEL")
[[ -n "${REASONING_EFFORT:-}" ]] && model_args+=(--reasoning-effort "$REASONING_EFFORT")
for v in "${pending[@]}"; do
  tag="${parent_tag}_${v}"
  if started "$v"; then
    echo "H ${v} over ${parent_tag} did not complete; continuing runs/${tag}."
  else
    echo "H ${v} over ${parent_tag} has not started; running it."
  fi
  rm -f "reports/${parent_tag}_${v^^}_GENERATION.md" "results/${b_tag}/${parent_name}_${v}.jsonl" \
        "results/${b_tag}/${parent_name}_${v}.provenance.json"
  common_args=(--parent-generation "$generation" --parent-scoring "$scoring"
               --feedback-judge "${FEEDBACK_JUDGE:-deepseek}" "${model_args[@]}"
               --run-root "runs/${tag}" --output "analysis/${tag}_generation.json")
  # Preflight (no model calls) logs locked, target and already-finished counts before any paid call.
  python "conditions/h/${v}/runner.py" "${common_args[@]}" 2>&1 | tee -a "logs/${b_tag}/${parent_name}_${v}.log"
  python "conditions/h/${v}/runner.py" --execute --resume "${common_args[@]}" \
    --workers "${WORKERS:-2}" --timeout "${TIMEOUT:-3600}" \
    --report "reports/${parent_tag}_${v^^}_GENERATION.md" \
    2>&1 | tee -a "logs/${b_tag}/${parent_name}_${v}.log"
  export_and_grade "$v"
done
echo "Finished: results/${b_tag}"
