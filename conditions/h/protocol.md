<!-- Path: `conditions/h/protocol.md`; Purpose: integrity protocol of condition H (feedback-assisted multihead rescue). -->
# Condition H protocol

1. **Parent.** The parent is one frozen generation, either B or a C level, with
   `freeze.status=frozen_before_private_scoring` and verified artifact hashes. It
   comes with the post-freeze scoring of that generation's export, bound by
   `source_generation_sha256`. The parent is never re-run. Its answers are exactly
   its exported rows.
2. **Lock.** A question is locked when its parent verdict is correct, accepted and
   resolved. Locked answers are never changed: every stage copies Yes results
   byte-for-byte, and the controller asserts it before freezing. All other
   questions are targets, and only capsules holding a target are run.
3. **Feedback.** Heads see only per-question `yes`/`no`. The parent's Yes/No comes
   from its scoring. Every later Yes/No comes from `../bixbench78_grader/score.py`,
   run as a separate process on a frozen predictions snapshot under
   `runs/<tag>/feedback/`. No H process opens the answer key, imports the grader,
   or writes ideal answers. The verdict ledger holds booleans only.
4. **Isolation.** Every model call is one OpenCode session in its own attempt
   container (`conditions/c/c_minimal/agent_call.run_agent`), under the live
   evaluator and path guard. The container mounts only that call's workspace;
   `feedback/` and the frozen parent are never mounted. Repair stages never retry.
   A fresh B head keeps B's single policy-interruption retry.
5. **Fallback.** A failed, interrupted or invalid stage keeps its prior result.
   A head whose fresh run fails falls back to all-unresolved. Its repair stages
   then start from that, and an unresolved answer is never selected.
6. **Freeze.** Before the first model call, the run root receives
   `protocol.json`, `prompt_snapshot.json` and `code_snapshot/`. After selection,
   the source hashes and the parent hash are re-verified, and every run-root
   artifact is hashed into `freeze.artifacts`. Capsule `data/`, `tmp/` and `home/`
   are excluded.
7. **Scoring.** The H export (`scripts/export_predictions.py --condition h`)
   contains:
   - the parent rows;
   - H answers only for replaced targets, marked `controller_accepted=true`;
   - `feedback_assisted: true`.

   It is graded post-freeze like every other condition.
8. **Interpretation.** H is a developmental, feedback-assisted port of ngch D. The
   v6.1 selector policy was tuned on a 24-capsule diagnostic set, and v7 is
   new. Neither result is answer-blind, and neither is evidence of causal
   superiority.
