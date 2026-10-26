<!-- Path: conditions/h/h_v61/prompts/b145_conservative.md; Purpose: legacy-head conservative Yes/No repair stage (verbatim from ngch conditions/d/heads/b145_conservative.md). -->
This is a single conservative Yes/No repair stage. Read `prior_result.json` and
`feedback.json`. Preserve every result marked Yes exactly. For each result marked
No, identify one concrete likely error in scope, filtering, joins, denominator,
statistic, entity mapping, unit, scale, precision, or public-resource version;
then rerun the required computation from the supplied capsule data. Do not infer
the answer from a No verdict and do not enumerate guesses. Persist substantive
code under `analysis/scripts/`. Return a complete capsule result in question
order; use unresolved when no defensible revision is available.
