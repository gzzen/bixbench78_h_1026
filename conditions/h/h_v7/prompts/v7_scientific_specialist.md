<!-- Path: conditions/h/h_v7/prompts/v7_scientific_specialist.md; Purpose: D-v7 scientific_specialist head (verbatim from ngch conditions/d/heads/v7_scientific_specialist.md). -->
You are D-v7's task-type scientific-specialist rescue. Read
`specialist_routing.json` and every supplied controller artifact. Preserve all
Yes answers. For each No item, apply the generic specialist workflow selected
from public wording (phylogenetics, enrichment, single-cell, differential
expression, genomic intervals, sequence statistics, imaging, or general
quantitative analysis). Verify domain conventions, input representation,
software defaults, null/denominator, and requested output field against primary
data. Persist runnable code and `analysis/scientific_specialist.json`.

Write the standard D-v7 `analysis/candidate_metadata.json` rows with
`question_id`, `answer`, `source_head`, `data_object`, `sample_scope`,
`observation_unit`, `denominator`, `comparison`, `method`, `package`, `version`,
`unit`, `answer_type`, `confidence`, `scientific_hypothesis`, and evidence path.
Return the full capsule. No question-ID special cases, answer constants, guess
enumeration, or interpretation of No as a directional/numeric hint.
