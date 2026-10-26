# Path: conditions/h/core/test_core.py
# Purpose: H core: parent loading (B and B+C), feedback oracle, Yes-locked repair stage, selection, prompt and key isolation.
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

from conditions.h.core import parent as h_parent
from conditions.h.core.aggregation import reference_free_select, sequential_yes_no_select
from conditions.h.core.feedback import FeedbackOracle
from conditions.h.core.stage import prompt_text, run_stage
from scripts import pack_run_artifacts as pack
from conditions.h.core.test_support import (
    ROOT, ROWS, FrozenParent, ScriptedAgent, capsule_payload, grader_runs, item, make_grader, offline_h,
)

# sha256 of the ngch source prompts (ngch_78_frozen_release_20261021/solver/conditions/d/heads/<name>.md).
SOURCE_PROMPTS = {
    "h_v61/prompts/b145_conservative.md": "f5f69cef04db2a65f698d5874723781f90fb846dbe7b289111f5110384fb777f",
    "h_v61/prompts/b145_aggressive.md": "f6ca215461f20299d5c2a5b8af3e0c7acd3290c4ac582d01f975108e90b75529",
    "h_v61/prompts/d4_data_forensics.md": "e30d83def578f4cea4cbaaf6c8568ff126e07135af5b975f00e305cc46054cd8",
    "h_v61/prompts/d4_method_triangulation.md": "74e30432141c281b424fe2705b2688f1e780a5e1f6197cdf3f04c3dcf844408f",
    "h_v7/prompts/v7_full_recompute.md": "03a182e54df2b66acc1f5535849f8d6109e5206709720e9053b198a8a6e05315",
    "h_v7/prompts/v7_simple_baseline.md": "30e24feccfb6307c8334530c41763c93e3b5b234f1c3ab24553f370e61227e33",
    "h_v7/prompts/v7_data_forensics.md": "87095db090a39359b6dab35f0d3013e123496b8080af7b05a320b9b7efae0ff0",
    "h_v7/prompts/v7_method_triangulation.md": "d9e9cfd0832d500a57518d9a9367de78be6f31b7b93f105cdece0ce39ef214f6",
    "h_v7/prompts/v7_alternative_grounding.md": "d0003cb6733122ef4ceb1432a1f5be3c2e22c5cba62737948eae7d87fd9d8476",
    "h_v7/prompts/v7_protocol_fidelity.md": "af719d248c7b7dca11e61f1beff6097188789f4113311b77373893a8b66ce6ef",
    "h_v7/prompts/v7_adversarial_recompute.md": "103fe959ca621d86881b9c459785eb0d13d6072802607b79ed8199361775f7ca",
    "h_v7/prompts/v7_scientific_specialist.md": "f1ce236c8a432b15fecbde7c1ab8b781f13351fbc885ea1efa0fc7ca70d1fcc4",
}
H_DIR = ROOT / "conditions/h"


class ParentTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FrozenParent()

    def tearDown(self):
        self.fixture.cleanup()

    def load(self, generation=None, scoring=None, registry=None):
        with offline_h(ScriptedAgent(lambda label, ws: None)):
            return h_parent.load_h_parent(generation or self.fixture.b_generation, scoring or self.fixture.b_scoring,
                                          registry)

    def test_b_parent_locks_scored_yes_and_triggers_only_capsules_with_targets(self):
        parent = self.load(registry=self.fixture.base / "registry.json")
        self.assertEqual(parent.locked, ["bix-1-q1", "bix-2-q1"])
        self.assertEqual(parent.targets, ["bix-1-q2"])
        self.assertEqual([c["short_id"] for c in parent.triggered()], ["bix-1"])
        capsule = parent.triggered()[0]
        self.assertEqual(parent.capsule_feedback(capsule), [{"question_id": "bix-1-q1", "feedback": "yes"},
                                                            {"question_id": "bix-1-q2", "feedback": "no"}])
        self.assertEqual(parent.capsule_payload(capsule)["results"][1]["candidates"][0]["answer"], "wrong")
        self.assertEqual(parent.registry["parent_kind"], "b")
        self.assertEqual(parent.registry["b_model"], "deepseek/deepseek-flash")
        self.assertTrue((self.fixture.base / "registry.json").is_file())

    def test_registry_mismatch_and_foreign_scoring_are_refused(self):
        registry = self.fixture.base / "registry.json"
        self.load(registry=registry)
        value = json.loads(registry.read_text())
        value["locked_question_ids"] = []
        registry.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, "registry mismatch"):
            self.load(registry=registry)
        scoring = json.loads(self.fixture.b_scoring.read_text())
        scoring["source_generation_sha256"] = "0" * 64
        self.fixture.b_scoring.write_text(json.dumps(scoring))
        with self.assertRaisesRegex(RuntimeError, "different parent generation"):
            self.load()

    def test_c_parent_core_is_the_scored_composite(self):
        c_generation, c_scoring = self.fixture.add_c_level("c_answer", correct=False)
        parent = self.load(c_generation, c_scoring)
        self.assertEqual((parent.registry["parent_kind"], parent.registry["c_level"]), ("c", "c_minimal"))
        self.assertEqual(parent.targets, ["bix-1-q2"])
        self.assertEqual(parent.core["bix-1-q2"]["candidates"][0]["answer"], "c_answer")
        self.assertEqual(parent.core["bix-1-q2"]["summary"], "c summary")
        self.assertEqual(parent.workspaces["cap-1"].name, "bix-1__cap-1")
        c_generation, c_scoring = self.fixture.add_c_level("right", correct=True)
        self.assertEqual(self.load(c_generation, c_scoring).targets, [])


class FeedbackOracleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.grader = make_grader(self.base / "grader")

    def tearDown(self):
        self.tmp.cleanup()

    def test_verdicts_come_from_the_grader_and_are_cached_in_the_ledger(self):
        oracle = FeedbackOracle(self.base / "run", self.grader, "surrogate")
        feedback = oracle.capsule_feedback(capsule_payload("bix-1", {"bix-1-q1": "12", "bix-1-q2": "nope"}), "s1")
        self.assertEqual([f["feedback"] for f in feedback], ["yes", "no"])
        self.assertTrue(oracle.submit("bix-1-q2", " RIGHT ", "s2"))
        self.assertFalse(oracle.submit("bix-1-q2", "", "s3"))
        self.assertTrue(oracle.submit("bix-1-q2", "RIGHT", "s4"))   # cached: no new grader run
        runs = grader_runs(self.grader)
        self.assertEqual([r["ids"] for r in runs], [["bix-1-q1", "bix-1-q2"], ["bix-1-q2"]])
        self.assertEqual({r["judge"] for r in runs}, {"surrogate"})
        resumed = FeedbackOracle(self.base / "run", self.grader, "surrogate")
        self.assertTrue(resumed.submit("bix-1-q1", "12", "again"))
        self.assertEqual(len(grader_runs(self.grader)), 2)
        self.assertEqual(resumed.calls, 2)
        ledger = [json.loads(line) for line in (self.base / "run/feedback/ledger.jsonl").read_text().splitlines()]
        self.assertTrue(all(set(entry) == {"key", "question_id", "correct", "grading"} for entry in ledger))

    def test_one_grader_run_holds_each_question_once(self):
        oracle = FeedbackOracle(self.base / "run", self.grader, "surrogate")
        verdicts = oracle.judge_many([("bix-1-q2", "a"), ("bix-1-q2", "right"), ("bix-1-q1", "12")], "batch")
        self.assertEqual(sum(verdicts.values()), 2)
        self.assertEqual([sorted(r["ids"]) for r in grader_runs(self.grader)],
                         [["bix-1-q1", "bix-1-q2"], ["bix-1-q2"]])

    def test_grader_failure_stops_the_run(self):
        oracle = FeedbackOracle(self.base / "run", self.base / "missing.py", "surrogate")
        with self.assertRaisesRegex(RuntimeError, "feedback grader failed"):
            oracle.submit("bix-1-q1", "12", "x")


class StageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rows = [dict(r) for r in ROWS if r["short_id"] == "bix-1"]
        self.prior = capsule_payload("bix-1", {"bix-1-q1": "12", "bix-1-q2": "wrong"})
        self.feedback = [{"question_id": "bix-1-q1", "feedback": "yes"}, {"question_id": "bix-1-q2", "feedback": "no"}]

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self, script, feedback=None):
        agent = ScriptedAgent(script)
        with offline_h(agent):
            final, record = run_stage(head="h", stage="s", prior=self.prior, feedback=feedback or self.feedback,
                                      rows=self.rows, root=self.root, timeout=5,
                                      prompt_path=H_DIR / "h_v61/prompts/b145_conservative.md")
        return final, record, agent

    def test_yes_results_are_locked_and_no_results_replaced(self):
        final, record, agent = self.stage(lambda label, ws: capsule_payload("bix-1", {"bix-1-q1": "13",
                                                                                    "bix-1-q2": "right"}))
        self.assertTrue(record["accepted"])
        self.assertEqual([r["candidates"][0]["answer"] for r in final["results"]], ["12", "right"])
        workspace = Path(record["workspace"])
        self.assertEqual(json.loads((workspace / "feedback.json").read_text())["items"], self.feedback)
        self.assertEqual(json.loads((workspace / "head_canonical.json").read_text()), final)
        self.assertNotIn("Path: conditions/h/", agent.calls[0]["prompt"])
        self.assertTrue(agent.calls[0]["prompt"].endswith(prompt_text(H_DIR / "h_v61/prompts/b145_conservative.md")))
        self.assertEqual(agent.calls[0]["label"], "H/h/s/bix-1")

    def test_failed_call_keeps_the_prior_and_all_yes_skips_the_call(self):
        final, record, _ = self.stage(lambda label, ws: {"code": 1})
        self.assertEqual((final, record["accepted"]), (self.prior, False))
        final, record, agent = self.stage(lambda label, ws: None, [dict(f, feedback="yes") for f in self.feedback])
        self.assertEqual((final, record["skipped"], record["total_agent_calls"], agent.calls), (self.prior, True, 0, []))


class SelectionTests(unittest.TestCase):
    def test_reference_free_keeps_parent_off_target_and_prefers_consensus(self):
        core = {"q1": item("q1", "a"), "q2": item("q2", "b")}
        heads = {"h1": {"q1": item("q1", "x"), "q2": item("q2", "1")},
                 "h2": {"q2": item("q2", "2")}, "h3": {"q2": item("q2", "2.0")}}
        final, decisions = reference_free_select(core, heads, {"q2"}, ("h1", "h2", "h3"))
        self.assertEqual(final["q1"], core["q1"])
        self.assertEqual(final["q2"]["candidates"][0]["answer"], "2")
        self.assertEqual(decisions[1], {"question_id": "q2", "source": "h2", "rule": "head_consensus"})
        self.assertEqual(decisions[0]["source"], "parent")

    def test_sequential_stops_at_first_yes(self):
        core = {"q1": item("q1", "a")}
        heads = {"h1": {"q1": item("q1", "b")}, "h2": {"q1": item("q1", "c")}}
        final, decisions = sequential_yes_no_select(core, heads, {"q1": False},
                                                    {"h1": {"q1": False}, "h2": {"q1": True}}, ("h1", "h2"))
        self.assertEqual((final["q1"]["candidates"][0]["answer"], decisions[0]["accepted_source"]), ("c", "h2"))


class IsolationTests(unittest.TestCase):
    def test_prompts_are_the_ngch_source_bytes(self):
        for relative, expected in SOURCE_PROMPTS.items():
            text = prompt_text(H_DIR / relative)
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), expected, relative)

    def test_no_h_source_reads_the_answer_key_or_imports_the_grader(self):
        forbidden = re.compile(r"answers_78|bixbench78_grader/answers|^\s*(?:from|import)\s+(?:judge|rules|score|deepseek)\b"
                               r"|yes_no_open_evaluator|\bjudge\(", re.M)
        for path in sorted(H_DIR.rglob("*.py")):
            if path.name.startswith("test_"):
                continue
            self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")), path)


class PackTests(unittest.TestCase):
    def test_h_workspaces_are_packed_and_labelled_but_feedback_is_not(self):
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            run_root = root / "runs/exp_b_c_minimal_h_v61"
            for relative in ("heads/full/conservative/bix-1__cap-1/head_canonical.json",
                             "heads/full/initial/0000/bix-1__cap-1/capsule_result.json",
                             "capsules/bix-1__cap-1/candidate_registry.jsonl",
                             "tasks/full__bix-1.json", "protocol.json",
                             "feedback/00001_x/scoring.json", "feedback/ledger.jsonl"):
                (run_root / relative).parent.mkdir(parents=True, exist_ok=True)
                (run_root / relative).write_text("{}")
            files = {path.relative_to(root).as_posix() for path in pack.workspace_artifacts(pack.run_roots(root, "exp"))}
            self.assertEqual(files, {f"runs/exp_b_c_minimal_h_v61/{r}" for r in (
                "heads/full/conservative/bix-1__cap-1/head_canonical.json",
                "heads/full/initial/0000/bix-1__cap-1/capsule_result.json",
                "capsules/bix-1__cap-1/candidate_registry.jsonl", "tasks/full__bix-1.json", "protocol.json")})
            self.assertTrue(pack.is_feedback_assisted("runs/exp_b_h_v7/tasks/bix-1.json"))
            self.assertTrue(pack.is_feedback_assisted("analysis/exp_b_h_parent.json"))
            self.assertFalse(pack.is_feedback_assisted("runs/exp_b/0000/bix-1__cap-1/capsule_result.json"))


if __name__ == "__main__":
    unittest.main()
