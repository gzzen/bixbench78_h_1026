# Path: conditions/c/c_minimal/test_parent.py
# Purpose: Frozen B parent: Yes lock, tamper rejection, scoring/generation identity and packet construction.
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from conditions.c.c_minimal import parent

QIDS = ["bix-1-q1", "bix-1-q2", "bix-1-q3"]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ParentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(parent, "ROOT", self.root),
                        patch.object(parent, "load_rows", lambda: [
                            {"question_id": q, "short_id": "bix-1", "capsule_uuid": "cap-1", "question": q} for q in QIDS]),
                        patch.object(parent, "public_question", lambda row: dict(row))]
        for item in self.patches:
            item.start()
        self.workspace = self.root / "runs/b/0000/bix-1__cap-1"
        (self.workspace / "analysis/scripts").mkdir(parents=True)
        (self.workspace / "capsule_result.json").write_text(json.dumps({"capsule_uuid": "cap-1", "short_id": "bix-1", "results": []}))
        (self.workspace / "analysis/scripts/run.py").write_text("print(1)\n")
        (self.workspace / "analysis/table.csv").write_text("a\n1\n")
        (self.workspace / "analysis/compliance_audit.json").write_text("{}")
        self.generation = self.root / "analysis/tag_b_generation.json"
        self.scoring = self.root / "analysis/tag_b_scoring.json"
        self.registry = self.root / "analysis/tag_c_parent.json"
        self.write_generation()
        self.write_scoring([True, False, True], accepted=[True, True, False])

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def write_generation(self, status="frozen_before_private_scoring", question_ids=QIDS, workspace=None):
        artifacts = [{"path": str(p.relative_to(self.root)), "sha256": sha(p)} for p in
                     (self.workspace / "capsule_result.json", self.workspace / "analysis/scripts/run.py")]
        self.generation.parent.mkdir(parents=True, exist_ok=True)
        self.generation.write_text(json.dumps({
            "manifest": {"model": "deepseek/deepseek-flash", "reasoning_effort": "high"},
            "episodes": [{"short_id": "bix-1", "capsule_uuid": "cap-1", "question_ids": question_ids,
                          "workspace": workspace or str(self.workspace), "accepted": True}],
            "freeze": {"status": status, "manifest_sha256": "m", "artifacts": artifacts}}))

    def write_scoring(self, correct, accepted=None):
        accepted = accepted or [True] * len(correct)
        self.scoring.write_text(json.dumps({"status": "completed_posthoc_after_freeze", "verdicts": [
            {"question_id": q, "correct": c, "accepted": a, "resolved": True}
            for q, c, a in zip(QIDS, correct, accepted)]}))

    def load(self):
        return parent.load_parent(self.generation, self.scoring, self.registry)

    def test_only_correct_accepted_resolved_is_locked(self):
        _, _, registry = self.load()
        self.assertEqual(registry["locked_yes_question_ids"], ["bix-1-q1"])
        self.assertEqual(registry["rescue_question_ids"], ["bix-1-q2", "bix-1-q3"])
        self.assertTrue(self.registry.is_file())

    def test_registry_is_shared_and_must_match(self):
        first = self.load()[2]
        self.assertEqual(self.load()[2], first, "another level reuses the identical registry")
        self.write_scoring([True, True, True])
        with self.assertRaisesRegex(RuntimeError, "parent manifest mismatch"):
            self.load()

    def test_tampered_frozen_artifact_is_rejected(self):
        (self.workspace / "analysis/scripts/run.py").write_text("print(2)\n")
        with self.assertRaisesRegex(RuntimeError, "frozen artifact changed"):
            self.load()

    def test_unfrozen_generation_is_rejected(self):
        self.write_generation(status="running")
        with self.assertRaisesRegex(RuntimeError, "not frozen"):
            self.load()

    def test_scoring_bound_to_another_generation_is_rejected(self):
        scoring = json.loads(self.scoring.read_text())
        self.scoring.write_text(json.dumps({**scoring, "source_generation_sha256": sha(self.generation)}))
        self.assertEqual(self.load()[2]["locked_yes_question_ids"], ["bix-1-q1"])
        self.registry.unlink()
        self.scoring.write_text(json.dumps({**scoring, "source_generation_sha256": "0" * 64}))
        with self.assertRaisesRegex(RuntimeError, "different B generation"):
            self.load()

    def test_run_tree_copied_from_another_checkout_reads_the_local_frozen_copy(self):
        self.write_generation(workspace="/elsewhere/old_checkout/runs/b/0000/bix-1__cap-1")
        generation, scoring, _ = self.load()
        records, _ = parent.make_packet_records(generation, scoring, self.root / "runs/tag_c_minimal")
        packet = Path(records[0][0]["workspace"])
        self.assertEqual(json.loads((packet / "primary_canonical.json").read_text())["short_id"], "bix-1")
        self.assertTrue((packet / "analysis/scripts/run.py").is_file())

    def test_missing_workspace_or_accepted_result_fails_preflight(self):
        self.write_generation(workspace="/elsewhere/old_checkout/runs/b/0000/bix-404__cap-1")
        with self.assertRaisesRegex(RuntimeError, "frozen workspace not found"):
            self.load()
        self.write_generation()
        (self.workspace / "capsule_result.json").unlink()
        with self.assertRaises(RuntimeError):
            self.load()

    def test_scoring_must_cover_the_generation_questions(self):
        self.write_generation(question_ids=QIDS[:2])
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            self.load()

    def test_packets_carry_frozen_evidence_but_not_audits(self):
        generation, scoring, _ = self.load()
        records, targets = parent.make_packet_records(generation, scoring, self.root / "runs/tag_c_minimal")
        self.assertEqual([t["question_id"] for t in targets], ["bix-1-q2", "bix-1-q3"])
        packet = Path(records[0][0]["workspace"])
        self.assertTrue((packet / "primary_canonical.json").is_file())
        self.assertTrue((packet / "analysis/scripts/run.py").is_file())
        self.assertTrue((packet / "analysis/table.csv").is_file())
        self.assertFalse((packet / "analysis/compliance_audit.json").exists())
        roles = {item["path"]: item["role"] for item in json.loads((packet / "imported_artifacts.json").read_text())}
        self.assertEqual(roles["analysis/scripts/run.py"], "B3 executed source")
        self.assertEqual(records[0][2], {"question_id": "bix-1-q2", "feedback": "no"})

    def test_missing_result_of_failed_b_episode_becomes_unresolved_primary_with_identity(self):
        generation, scoring, _ = self.load()
        (self.workspace / "capsule_result.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "accepted B episode has no frozen capsule_result"):
            parent.make_packet_records(generation, scoring, self.root / "runs/tag_c_minimal", {"bix-1-q2"})
        generation["episodes"][0]["accepted"] = False
        records, _ = parent.make_packet_records(generation, scoring, self.root / "runs/tag_c_minimal_2", {"bix-1-q2"})
        primary = json.loads((Path(records[0][0]["workspace"]) / "primary_canonical.json").read_text())
        self.assertEqual((primary["capsule_uuid"], primary["short_id"]), ("cap-1", "bix-1"))
        self.assertTrue(all(item["status"] == "unresolved" for item in primary["results"]))

    def test_unfrozen_parent_script_is_rejected(self):
        generation, scoring, _ = self.load()
        (self.workspace / "analysis/scripts/extra.py").write_text("print(3)\n")
        with self.assertRaisesRegex(RuntimeError, "parent source not frozen"):
            parent.make_packet_records(generation, scoring, self.root / "runs/tag_c_minimal")


class GraderIsolationTests(unittest.TestCase):
    # Condition H's in-run Yes/No runs the sibling grader as a separate process on frozen
    # snapshots; that one module may name it, but never the answer key or an evaluator.
    GRADER_SUBPROCESS = "conditions/h/core/feedback.py"

    def test_generation_code_never_references_the_grader_or_answer_key(self):
        root = Path(__file__).resolve().parents[3]
        needles = ("bixbench78_grader", "answers_78", "yes_no_open_evaluator")
        offenders = []
        for top in ("conditions", "common"):
            for path in (root / top).rglob("*.py"):
                relative = str(path.relative_to(root))
                if path.name.startswith("test_"):
                    continue
                allowed = needles[1:] if relative == self.GRADER_SUBPROCESS else needles
                if any(needle in path.read_text(encoding="utf-8") for needle in allowed):
                    offenders.append(relative)
        self.assertEqual(offenders, [])
        self.assertIn("bixbench78_grader", (root / self.GRADER_SUBPROCESS).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
