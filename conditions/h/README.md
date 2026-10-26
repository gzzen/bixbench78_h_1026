<!-- Path: `conditions/h/README.md`; Purpose: overview of condition H (ports of ngch D-v6.1 and D-v7) over a frozen B or B+C parent. -->
# Condition H: multihead rescue over a frozen B or C parent

H is a **feedback-assisted** condition. It never reruns its parent. It reads one
frozen generation, either B or a C level over B, together with the post-freeze
scoring of that generation's export. It keeps every parent Yes (correct, accepted
and resolved) unchanged and runs several independent repair heads on each capsule
that still holds a No. Final score = parent compliant correct + H rescues.

This is a port of condition D from the frozen `ngch_78_frozen_release_20261021`
package (`solver/conditions/d`), moved from Codex to this checkout's OpenCode v2
per-attempt Docker boundary. Two of D's variants are ported. Their head prompts
are byte-identical to the source; `core/test_core.py` pins their sha256.

| Variant | Source | Per triggered capsule | Final answer |
|---|---|---|---|
| `h_v61` | `runner_v61_fresh.py` (D-v6.1 balanced) | 5 head families in parallel, up to 11 calls | consensus of the selector heads, else their priority (or `--selection-mode sequential_yes_no`) |
| `h_v7` | `runner_v7.py` (D-v7 localized) | localized heads plus rescue chain, sequential, up to 9 calls | the first ranked candidate that gets a Yes |

## How H keeps the answer key out of generation

In ngch, D called the private judge against the answer key inside the run. Here the
key exists only in the sibling `../bixbench78_grader`, and every Yes/No H needs is
produced the way a final export is scored:

1. The answers to judge are frozen into `runs/<tag>/feedback/<n>_<label>/predictions.jsonl`
   plus `provenance.json` (`frozen_before_private_scoring`).
2. `../bixbench78_grader/score.py` runs as a separate process.
3. Only its `verdicts[].correct` booleans are read back, and a head receives only
   `{"question_id", "feedback": "yes"|"no"}` items.

`feedback/` lives in the run root, outside every attempt container's mount.
`feedback/ledger.jsonl` stores one verdict per (question, answer), so a repeated
or resumed submission is never graded twice. `--feedback-judge surrogate` grades
`llm_verifier` questions with the grader's offline rule instead of DeepSeek.

The parent's own Yes/No comes from its scoring. A target whose parent answer is
correct but non-compliant therefore counts as a No, exactly as it does in the
parent score.

## Layout

```text
conditions/h/
├── core/                 shared by both variants
│   ├── parent.py         load/verify frozen B or C + scoring; Yes lock; targets; shared registry
│   ├── feedback.py       FeedbackOracle: sibling-grader Yes/No on frozen snapshots; ledger
│   ├── stage.py          one isolated OpenCode Yes/No repair stage with byte-for-byte Yes lock; model setup
│   ├── aggregation.py    reference-free, sequential Yes/No and oracle selectors (ngch exploration/aggregation.py)
│   ├── answer_contract.py  deterministic 1dp/0dp answer variants (ngch modules/answer_contract.py)
│   ├── rescue_main.py    CLI, protocol + code snapshot, resumable task pool, freeze, report
│   └── test_core.py, test_support.py
├── h_v61/                policy.py (head_policy_v6), heads.py, pipeline.py, prompts/, runner.py, config.json, test
└── h_v7/                 contracts.py, localizer.py, controller.py (ngch v7_*), pipeline.py, prompts/, runner.py,
                          config.json, test
```

Imports only go downward: each variant uses `core`, and `core` uses `common`,
`conditions.a`, `conditions.b`, `conditions.c.c_minimal` (the isolated agent call)
and `scripts/export_predictions.py`. The parent's export rows are the definition of
the parent answers. A run snapshots `core` plus its variant (`protocol.json` →
`sources`, `code_snapshot/`).

## h_v61: five head families (D-v6.1)

| Head | Start | Repair stages (each one call, Yes locked) |
|---|---|---|
| `full` | fresh B capsule run | `conservative` (`b145_conservative.md`) |
| `baseline_polling` | fresh A capsule run | `poll_1` (`b145_conservative.md`) |
| `full_aggressive` | fresh B capsule run | `conservative`, then `aggressive_1` (`b145_aggressive.md`) |
| `data_forensics` | frozen parent answer | `structural_reflection` (`d4_data_forensics.md`) |
| `method_triangulation` | frozen parent answer | `structural_reflection` (`d4_method_triangulation.md`) |

The legacy heads (`full`, `baseline_polling`, `full_aggressive`) are judged by the
feedback oracle before each repair. The reflection heads use the parent's Yes/No.
Each head also yields deterministic `contract_1dp__*` and `contract_0dp__*` variants.

The default selection is `reference_free_select` over `SELECTOR_HEADS`
(`contract_1dp__full`, `contract_1dp__method_triangulation`, `contract_1dp__full_aggressive`).
This takes a normalized-answer consensus of 2 or more, else the first available
answer in that order. `--selection-mode sequential_yes_no` (D-v6.2) instead submits
unique head answers in order until one gets a Yes. Every head's result stays in the
generation (`tasks[].final`), so the coverage-oracle endpoints can be scored post hoc.

## h_v7: localized cumulative repair (D-v7)

For one triggered capsule:

1. Register the parent answers. Yes questions are accepted at stage `parent`.
2. Localize failures from public metadata and the Yes/No pattern (`localizer.py`).
3. Submit deterministic answer-contract variants.
4. Run early protocol fidelity when a question names a protocol.
5. Run the localized initial heads (a subset of full_recompute, simple_baseline,
   data_forensics, method_triangulation and alternative_grounding).
6. Analyze head disagreement.
7. Run the rescue heads as long as a No remains: deferred protocol fidelity,
   adversarial recompute, semantic regrounding, then scientific specialist.
8. Optionally (`--enable-bounded`), submit explicit public integer domains.

After every stage, pending candidates are ranked (`controller.rank_candidates`).
They are submitted to the oracle one at a time until one gets a Yes, and each
normalized value is submitted at most once per question.

Differences from ngch, all forced by the frozen parent:
- The parent is not re-run, so the D-v7 pre-execution note on the fresh B prompt
  does not apply. Heads still receive `protocol_spec.json` and `answer_contract.json`.
- Localization, disagreement, bounded-search files and a per-capsule
  `candidate_registry.jsonl` go to `runs/<tag>/capsules/<slug>/`. They never go
  into the frozen B workspace.
- `--ablation` (5 values), `--enable-bounded` and `--max-bounded-domain` are kept.

## Running

```bash
# Over c_minimal (the default parent), after scripts/run_c.sh "$tag":
scripts/run_h.sh "$tag"                      # h_v61 over <tag>_c_minimal
scripts/run_h.sh "$tag" all c_minimal        # h_v61 and h_v7
scripts/run_h.sh "$tag" h_v7 none            # h_v7 directly over B
# Preflight only (no model calls, no grader calls):
python conditions/h/h_v61/runner.py --parent-generation analysis/<tag>_c_minimal_generation.json \
  --parent-scoring analysis/<tag>_c_minimal_scoring.json
```

`run_h.sh` grades the parent export with the sibling grader if its scoring is
missing. It then runs preflight followed by `--execute --resume`, exports the H
generation (`scripts/export_predictions.py --condition h`) and grades it. Its
outputs are:
- `runs/<parent>_<variant>/`
- `analysis/<parent>_<variant>_{generation,scoring}.json`
- `results/<tag>/<c_level|b>_<variant>.jsonl`

Rerunning it skips completed variants. An interrupted variant continues: finished
tasks are kept (`runs/<tag>/tasks/<task>.json` is written last), and unfinished
ones restart in clean workspaces. Grader verdicts already in the ledger are reused.
`FRESH=1` redoes the selected variants. Both variants over one parent share
`analysis/<parent>_h_parent.json`, so they target the same questions.

## Tests (offline)

```bash
python -m unittest conditions.h.core.test_core conditions.h.h_v61.test_h_v61 conditions.h.h_v7.test_h_v7
```

They use a fake frozen B/C parent under `runs/`, a fake `score.py`, and a scripted
fake OpenCode agent; no model, Docker or DeepSeek call is made.
