# Path: conditions/h/h_v61/policy.py
# Purpose: Evidence-based D-v6.1 head policy (verbatim port of ngch conditions/d/modules/head_policy_v6.py).
"""Evidence-based D-v6.1 head policy for the lean24 diagnostic set."""

# These three families are the smallest set retaining the observed 53/96 Oracle
# coverage after answer-contract variants are grouped with their parent head.
COVERAGE_HEADS = (
    "full",
    "baseline_polling",
    "data_forensics",
)

# These three 1dp contract variants form the strongest observed no-verdict
# selector subset (46/96).  The choice is diagnostic-set-tuned and must be
# revalidated out of sample before being treated as a general policy.
SELECTOR_HEADS = (
    "contract_1dp__full",
    "contract_1dp__method_triangulation",
    "contract_1dp__full_aggressive",
)

# Union of coverage and selector roles.  Heads outside this set had zero
# leave-one-family-out Oracle loss and did not improve the best selector subset.
EXECUTION_HEADS = (
    "full",
    "baseline_polling",
    "data_forensics",
    "method_triangulation",
    "full_aggressive",
)

ESTIMATED_CALLS_FROM_SCRATCH = {
    "b_core": 24,
    "full": 44,
    "baseline_polling": 44,
    "data_forensics": 22,
    "method_triangulation": 22,
    "full_aggressive": 65,
}

