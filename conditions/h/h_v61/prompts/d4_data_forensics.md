<!-- Path: conditions/h/h_v61/prompts/d4_data_forensics.md; Purpose: data_forensics reflection head (verbatim from ngch conditions/d/heads/d4_data_forensics.md). -->
Repair the frozen B result as a data-forensics specialist. You receive only
per-question Yes/No correctness bits; preserve every Yes item byte-for-byte.
For each No, inventory every supplied file and relevant sheet/table; profile
schema, types, missing values, duplicates, identifiers, factor levels, sample
labels, coordinate conventions, and join cardinalities. Trace each requested
quantity back to source rows and record intermediate row counts in
`analysis/data_forensics.json`. Recompute with persisted executable code and
cross-check the final number against an independent direct aggregation whenever
possible. Do not guess from feedback or enumerate candidate answers. Return a
complete result in original question order.

