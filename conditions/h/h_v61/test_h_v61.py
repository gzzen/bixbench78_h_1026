# Path: conditions/h/h_v61/test_h_v61.py
# Purpose: h_v61 end to end offline: preflight, five heads, oracle-gated repairs, selection, freeze, export and resume.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from common.benchmark.a84_contract import write_json
from conditions.h.core.test_support import (
    ROOT, FrozenParent, ScriptedAgent, capsule_payload, grader_runs, run_main, sha,
)
from conditions.h.h_v61 import heads, policy
from conditions.h.h_v61.pipeline import BalancedV61
from scripts import export_predictions

# Fresh initial answers of the legacy heads and the q2 answer each repair stage returns.
INITIAL = {"full": "wrong2", "baseline_polling": "p0", "full_aggressive": "x"}
REPAIR = {("full", "conservative"): "right", ("baseline_polling", "poll_1"): "poll",
          ("data_forensics", "structural_reflection"): "df",
          ("method_triangulation", "structural_reflection"): "right",
          ("full_aggressive", "conservative"): "y", ("full_aggressive", "aggressive_1"): "right"}


def fake_initial(head, item, rows, root, timeout):
    workspace = root / "heads" / head / "initial/0000" / f"{item['short_id']}__{item['capsule_uuid']}"
    workspace.mkdir(parents=True)
    write_json(workspace / "capsule_result.json", capsule_payload("bix-1", {"bix-1-q1": "12",
                                                                            "bix-1-q2": INITIAL[head]}))
    return {"short_id": item["short_id"], "capsule_uuid": item["capsule_uuid"], "workspace": str(workspace),
            "accepted": True, "total_agent_calls": 1, "controller_status": "passed"}


def script(label, workspace):
    _, head, stage, _ = label.split("/")
    # q1 is Yes everywhere; the "changed" answer must be reverted by the Yes lock.
    return capsule_payload("bix-1", {"bix-1-q1": "changed", "bix-1-q2": REPAIR[(head, stage)]})


class PolicyTests(unittest.TestCase):
    def test_policy_matches_the_released_d_v61(self):
        self.assertEqual(policy.EXECUTION_HEADS, ("full", "baseline_polling", "data_forensics",
                                                  "method_triangulation", "full_aggressive"))
        self.assertEqual(policy.SELECTOR_HEADS, ("contract_1dp__full", "contract_1dp__method_triangulation",
                                                 "contract_1dp__full_aggressive"))
        self.assertEqual(policy.COVERAGE_HEADS, ("full", "baseline_polling", "data_forensics"))
        self.assertEqual(set(heads.LEGACY_HEADS) | set(heads.REFLECTION_PROMPTS), set(policy.EXECUTION_HEADS))


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FrozenParent()
        self.initial = patch.object(heads, "fresh_initial", fake_initial)
        self.initial.start()

    def tearDown(self):
        self.initial.stop()
        self.fixture.cleanup()

    def run_v61(self, *extra, agent=None):
        return run_main(BalancedV61(), self.fixture, "--workers", "3", *extra, agent=agent)

    def test_preflight_makes_no_calls_and_counts_one_triggered_capsule(self):
        agent = ScriptedAgent(script)
        summary = self.run_v61(agent=agent)
        self.assertEqual(agent.calls, [])
        self.assertEqual((summary["locked"], summary["targets"], summary["triggered_capsules"], summary["tasks"]),
                         (2, 1, 1, 5))
        self.assertEqual(summary["model"], "deepseek/deepseek-flash")
        self.assertFalse((self.fixture.base / "h_run").exists())

    def test_execute_selects_by_selector_consensus_and_freezes(self):
        agent = ScriptedAgent(script)
        summary = self.run_v61("--execute", agent=agent)
        self.assertEqual(sorted(call["label"] for call in agent.calls), sorted(
            f"H/{head}/{stage}/bix-1" for head, stage in REPAIR))
        self.assertEqual((summary["h_replaced_answers"], summary["total_agent_calls"]), (1, 3 + 6))
        output = json.loads((self.fixture.base / "h_generation.json").read_text())
        self.assertEqual(output["freeze"]["status"], "frozen_before_private_scoring")
        self.assertEqual(output["h_replaced"], {"bix-1-q2": {"source": "contract_1dp__full", "rule": "head_consensus"}})
        self.assertEqual(output["final_results"]["bix-1-q2"]["candidates"][0]["answer"], "right")
        self.assertEqual(output["final_results"]["bix-1-q1"]["candidates"][0]["answer"], "12")
        full = next(r for r in output["tasks"] if r["head"] == "full")
        self.assertEqual([r["candidates"][0]["answer"] for r in full["final"]["results"]], ["12", "right"])
        self.assertEqual(full["transcript"][0]["feedback"][1], {"question_id": "bix-1-q2", "feedback": "no"})
        # Legacy heads asked the oracle about their own fresh answers; reflection heads used the parent Yes/No.
        graded = [r["ids"] for r in grader_runs(self.fixture.grader)]
        self.assertTrue(graded and all(set(ids) <= {"bix-1-q1", "bix-1-q2"} for ids in graded))
        for call in agent.calls:
            self.assertNotIn("ledger.jsonl", call["files"])
        for artifact in output["freeze"]["artifacts"]:
            self.assertEqual(sha(ROOT / artifact["path"]), artifact["sha256"])
        protocol = json.loads((self.fixture.base / "h_run/protocol.json").read_text())
        paths = {entry["path"] for entry in protocol["sources"]}
        self.assertIn("conditions/h/core/stage.py", paths)
        self.assertIn("conditions/h/h_v61/prompts/b145_conservative.md", paths)
        self.assertFalse(any(Path(p).name.startswith("test_") for p in paths))
        self.export_and_check(output)

    def export_and_check(self, output):
        rows, extra = export_predictions.h_rows(output, "tag_h")
        by_id = {row["question_id"]: row for row in rows}
        self.assertEqual(len(rows), 3)
        self.assertTrue(by_id["bix-1-q2"]["h_replaced"] and by_id["bix-1-q2"]["controller_accepted"])
        self.assertEqual(by_id["bix-1-q2"]["candidates"][0]["answer"], "right")
        self.assertTrue(by_id["bix-1-q1"]["parent_locked"] and not by_id["bix-1-q1"]["h_replaced"])
        self.assertEqual({row["condition"] for row in rows}, {"h"})
        self.assertEqual((extra["h_replaced_answers"], extra["feedback_assisted"], extra["parent_kind"]), (1, True, "b"))

    def test_sequential_mode_submits_until_yes(self):
        summary = self.run_v61("--execute", "--selection-mode", "sequential_yes_no", agent=ScriptedAgent(script))
        output = json.loads((self.fixture.base / "h_generation.json").read_text())
        self.assertEqual(summary["h_replaced_answers"], 1)
        self.assertEqual(output["h_replaced"]["bix-1-q2"]["source"], "contract_1dp__full")
        self.assertEqual(output["selection"]["decisions"][1]["accepted_source"], "contract_1dp__full")

    def test_resume_keeps_finished_tasks_and_redoes_the_rest(self):
        self.run_v61("--execute", agent=ScriptedAgent(script))
        run_root = self.fixture.base / "h_run"
        (self.fixture.base / "h_generation.json").unlink()
        (run_root / "tasks/full_aggressive__bix-1.json").unlink()   # interrupted before its record
        agent = ScriptedAgent(script)
        with self.assertRaises(SystemExit):
            self.run_v61("--execute", agent=agent)
        summary = self.run_v61("--execute", "--resume", agent=agent)
        self.assertEqual(sorted(call["label"] for call in agent.calls),
                         ["H/full_aggressive/aggressive_1/bix-1", "H/full_aggressive/conservative/bix-1"])
        self.assertEqual((summary["resumed_finished_tasks"], summary["h_replaced_answers"]), (4, 1))

    def test_c_parent_composite_is_exported_with_its_level(self):
        c_generation, c_scoring = self.fixture.add_c_level("c_answer", correct=False)
        run_main(BalancedV61(), self.fixture, "--workers", "2", "--execute", generation=c_generation,
                 scoring=c_scoring, agent=ScriptedAgent(script))
        output = json.loads((self.fixture.base / "h_generation.json").read_text())
        self.assertEqual(output["parent"]["parent_kind"], "c")
        reflection = next(r for r in output["tasks"] if r["head"] == "data_forensics")
        prior = json.loads((Path(reflection["stages"][0]["workspace"]) / "prior_result.json").read_text())
        self.assertEqual(prior["results"][1]["candidates"][0]["answer"], "c_answer")
        rows, extra = export_predictions.h_rows(output, "tag_h")
        self.assertEqual((extra["parent_kind"], extra["c_level"], extra["locked_parent_questions"]), ("c", "c_minimal", 2))
        by_id = {row["question_id"]: row for row in rows}
        self.assertEqual(by_id["bix-1-q2"]["candidates"][0]["answer"], "right")
        self.assertEqual((by_id["bix-1-q2"]["c_level"], by_id["bix-1-q2"]["h_variant"]), ("c_minimal", "h_v61"))

if __name__ == "__main__":
    unittest.main()
