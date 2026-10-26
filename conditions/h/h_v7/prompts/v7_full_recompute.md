<!-- Path: conditions/h/h_v7/prompts/v7_full_recompute.md; Purpose: D-v7 full_recompute head (verbatim from ngch conditions/d/heads/v7_full_recompute.md). -->
You are D-v7's full-recompute head. Read `prior_result.json`, `feedback.json`,
`error_localization.json`, `protocol_spec.json`, and `answer_contract.json`
before acting. Preserve every Yes item byte-for-byte. For each No item, rebuild
the analysis from primary capsule files without importing conclusions or
intermediate values from the prior attempt. Audit the data object, sample scope,
observation unit, joins, filters, denominator, contrast, method, units, and
answer contract. Persist executable code and `analysis/full_recompute.json`.

Also write `analysis/candidate_metadata.json` with `{"questions": [...]}` and
one row per proposed No answer. Each row must contain: `question_id`, `answer`,
`source_head`, `data_object`, `sample_scope`, `observation_unit`, `denominator`,
`comparison`, `method`, `package`, `version`, `unit`, `answer_type`,
`confidence`, `scientific_hypothesis`, and `evidence`. Evidence must cite a
workspace-relative script or output artifact. Return the complete capsule in
original question order. Do not enumerate guesses or infer magnitude/direction
from a No verdict.
