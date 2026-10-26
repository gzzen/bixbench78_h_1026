# bixbench78_h_1026: Native/B/C 78-question generation code plus condition H

A mirror of `bixbench78_c_1022` (its full history is kept; the `c1022` remote points at it)
with condition H added: ports of the ngch D-v6.1 and D-v7 multihead rescues over a frozen
B or B+C parent (`conditions/h/`).

Generation code only. This directory contains no generated
output, reports, frozen predictions, diagnostics, run workspaces, or staging
files. It also contains no answer key or scorer. Frozen, exported predictions
are scored by the private sibling repository `../bixbench78_grader/` (answer key
plus DeepSeek grader), which scripts call by relative path only after freeze.

- `conditions/`: Native and B runners, prompts, schemas and protocols; C rescue
  levels over a frozen, privately scored B run (`conditions/c/`); H variants over a frozen,
  privately scored B or C run (`conditions/h/`)
- `common/`: isolation boundary, host gateway, OpenCode adapter and tests
- `environment/`: `agent_bix` Conda environment specification
- `scripts/`: run (`run_native.sh`, `run_b.sh`, `run_c.sh`, `run_h.sh`), continue, progress, export and recovery tools
- `protocols/`: experiment substrate notes

Fresh generation uses OpenCode v2 with either the direct DeepSeek API
(`deepseek/deepseek-flash`) or an OpenAI Responses relay
(`openai-relay/gpt-5.6-sol`). See [OPENCODE_DEEPSEEK.md](OPENCODE_DEEPSEEK.md)
for setup, credentials, diagnostics and run commands.

Restore the public `data/` tree as a sibling of this directory before
generating; the code resolves `data/` relative to this directory's parent.
Runs create `analysis/`, `reports/`, `results/`, `runs/` and `logs/` here,
and `.gitignore` keeps them out of Git.

Condition C (`conditions/c/`) is feedback-assisted. It reads a frozen B generation
plus that run's post-freeze scoring (made by `../bixbench78_grader/score.py`) and
repairs only the
B-No questions in isolated OpenCode calls. Its three levels are nested:
`c_minimal` (base, holds the core) ⊂ `c_single` (+ semantic audit) ⊂ `c_blind`
(+ parent-blind contract). See [conditions/c/README.md](conditions/c/README.md).

Condition H (`conditions/h/`) is feedback-assisted too. It reads a frozen B or C-level
generation plus the post-freeze scoring of its export, keeps every parent Yes, and runs
several independent repair heads on each capsule that still holds a No. Two variants:
`h_v61` (D-v6.1 balanced: five head families, consensus selection) and `h_v7` (D-v7:
localized heads, rescue chain, ranked Yes/No submissions). Every in-run Yes/No comes from
`../bixbench78_grader/score.py` run on a frozen snapshot; no H process reads the answer key.
See [conditions/h/README.md](conditions/h/README.md).
