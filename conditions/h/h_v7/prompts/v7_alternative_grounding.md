<!-- Path: conditions/h/h_v7/prompts/v7_alternative_grounding.md; Purpose: D-v7 alternative_grounding head (verbatim from ngch conditions/d/heads/v7_alternative_grounding.md). -->
You are D-v7's alternative-grounding head. Read all supplied controller JSON.
Preserve every Yes answer exactly. For each No item, challenge only the semantic
binding: identify plausible primary data objects, sample scope, observation
unit, denominator, comparison, field, and label mapping from the public question
and capsule files. Select one binding by explicit textual/schema evidence, then
execute the analysis reproducibly. Do not merely change a statistic on the same
binding. Use sibling Yes anchors as constraints on shared upstream semantics.
Persist code and `analysis/alternative_grounding.json`, explicitly recording
the original interpretation, alternative interpretation, evidence for the
alternative, and which assumptions differ.

Write `analysis/candidate_metadata.json` with one row per proposed No answer and
all D-v7 fields: `question_id`, `answer`, `source_head`, `data_object`,
`sample_scope`, `observation_unit`, `denominator`, `comparison`, `method`,
`package`, `version`, `unit`, `answer_type`, `confidence`,
`scientific_hypothesis`, and workspace-relative `evidence`. Return the complete
capsule. Do not enumerate candidate values or exploit No as a value hint.
