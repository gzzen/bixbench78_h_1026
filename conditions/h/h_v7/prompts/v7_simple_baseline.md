<!-- Path: conditions/h/h_v7/prompts/v7_simple_baseline.md; Purpose: D-v7 simple_baseline head (verbatim from ngch conditions/d/heads/v7_simple_baseline.md). -->
You are D-v7's simple-baseline head. Read all supplied JSON control artifacts.
Preserve every Yes answer exactly. For each No question, ignore elaborate prior
machinery and implement the shortest transparent analysis justified by the
public wording and supplied data: direct filtering, counting, grouping, or a
plain standard test. State every assumption and validate the final quantity
against raw rows. Persist executable code and `analysis/simple_baseline.json`.

Write `analysis/candidate_metadata.json` in the same format required by D-v7:
one row per proposed No answer with `question_id`, `answer`, `source_head`,
`data_object`, `sample_scope`, `observation_unit`, `denominator`, `comparison`,
`method`, `package`, `version`, `unit`, `answer_type`, `confidence`,
`scientific_hypothesis`, and workspace-relative `evidence`. Return the full
capsule. Never turn No into a value hint and never enumerate guesses.
