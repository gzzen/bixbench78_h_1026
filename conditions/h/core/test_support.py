# Path: conditions/h/core/test_support.py
# Purpose: Offline fixtures for H tests: a frozen B (and C) parent under runs/, a fake grader and a scripted fake agent.
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

from conditions.c.c_minimal import agent_call
from conditions.c.c_minimal.test_support import fake_prepare, offline, text
from conditions.h.core import parent as h_parent
from conditions.h.core import stage

ROOT = Path(__file__).resolve().parents[3]
ROWS = [
    {"question_id": "bix-1-q1", "short_id": "bix-1", "capsule_uuid": "cap-1", "question": "How many genes pass?"},
    {"question_id": "bix-1-q2", "short_id": "bix-1", "capsule_uuid": "cap-1",
     "question": "What is the median expression across all samples?"},
    {"question_id": "bix-2-q1", "short_id": "bix-2", "capsule_uuid": "cap-2", "question": "Which gene is top?"},
]
KEY = {"bix-1-q1": "12", "bix-1-q2": "right", "bix-2-q1": "TP53"}
# Fake grader: exact, case-insensitive match against key.json beside it; every run is counted in runs.log.
GRADER = '''import argparse, json, pathlib
p = argparse.ArgumentParser()
for flag in ("--predictions", "--provenance", "--output", "--judge"):
    p.add_argument(flag)
a = p.parse_args()
here = pathlib.Path(__file__).resolve().parent
key = json.loads((here / "key.json").read_text())
prov = json.loads(pathlib.Path(a.provenance).read_text())
assert prov["freeze_status"] == "frozen_before_private_scoring" and prov["generation_scorer_or_evaluator"] is False
rows = [json.loads(line) for line in pathlib.Path(a.predictions).read_text().splitlines() if line.strip()]
ids = [r["question_id"] for r in rows]
assert len(ids) == len(set(ids)), "duplicate question IDs"
verdicts = [{"question_id": r["question_id"], "present": True, "accepted": True, "resolved": True,
             "correct": r["candidates"][0]["answer"].strip().casefold() == key[r["question_id"]].casefold()}
            for r in rows]
out = pathlib.Path(a.output)
assert not out.exists()
out.write_text(json.dumps({"status": "completed_posthoc_after_freeze", "verdicts": verdicts}))
with (here / "runs.log").open("a") as log:
    log.write(json.dumps({"judge": a.judge, "ids": ids}) + "\\n")
'''


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def item(qid: str, answer: str | None, summary: str = "summary") -> dict[str, Any]:
    if answer is None:
        return {"question_id": qid, "status": "unresolved", "candidates": [], "summary": summary}
    return {"question_id": qid, "status": "resolved",
            "candidates": [{"answer": answer, "method": "method", "evidence": "analysis/scripts/a.py"}],
            "summary": summary}


def capsule_payload(short_id: str, answers: dict[str, str | None]) -> dict[str, Any]:
    return {"capsule_uuid": "cap-" + short_id.split("-")[1], "short_id": short_id, "workflow_summary": "w",
            "results": [item(qid, answer) for qid, answer in answers.items()]}


def make_grader(directory: Path, key: dict[str, str] = KEY) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "key.json").write_text(json.dumps(key))
    (directory / "score.py").write_text(GRADER)
    return directory / "score.py"


def grader_runs(grader: Path) -> list[dict]:
    log = grader.parent / "runs.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


class FrozenParent:
    """A frozen B run (bix-1: q1 Yes, q2 No; bix-2: q1 Yes) and optionally a C level over it, under runs/."""

    def __init__(self, b_answers: dict[str, str | None] | None = None):
        self.created_runs = not (ROOT / "runs").exists()
        (ROOT / "runs").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "runs", prefix="h_test_")
        self.base = Path(self.tmp.name)
        answers = b_answers or {"bix-1-q1": "12", "bix-1-q2": "wrong", "bix-2-q1": "TP53"}
        episodes, artifacts = [], []
        for short_id, uuid in (("bix-1", "cap-1"), ("bix-2", "cap-2")):
            qids = [row["question_id"] for row in ROWS if row["short_id"] == short_id]
            workspace = self.base / f"b/0000/{short_id}__{uuid}"
            (workspace / "analysis/scripts").mkdir(parents=True)
            (workspace / "capsule_result.json").write_text(json.dumps(
                capsule_payload(short_id, {qid: answers[qid] for qid in qids})))
            (workspace / "analysis/grounding.json").write_text(json.dumps({"capsule_uuid": uuid, "questions": [
                {"question_id": qid, "data_object": "expr.tsv", "observation_unit": "sample",
                 "denominator_or_population": "all", "comparison": "none", "metric_or_outcome": "m",
                 "method_or_estimator": "median"} for qid in qids]}))
            (workspace / "analysis/scripts/b.py").write_text("print('b')\n")
            artifacts += [{"path": str(p.relative_to(ROOT)), "sha256": sha(p)}
                          for p in (workspace / "capsule_result.json", workspace / "analysis/scripts/b.py")]
            episodes.append({"short_id": short_id, "capsule_uuid": uuid, "question_ids": qids,
                             "workspace": str(workspace), "accepted": True, "controller_status": "passed"})
        self.b_generation = self.base / "tag_b_generation.json"
        self.b_generation.write_text(json.dumps({
            "manifest": {"model": "deepseek/deepseek-flash", "reasoning_effort": "high"},
            "episodes": episodes,
            "freeze": {"status": "frozen_before_private_scoring", "manifest_sha256": "m", "artifacts": artifacts}}))
        self.b_scoring = self.base / "tag_b_scoring.json"
        self.b_scoring.write_text(json.dumps(self.scoring(self.b_generation, {
            qid: answers[qid] is not None and answers[qid] == KEY[qid] for qid in KEY})))
        self.grader = make_grader(self.base / "grader")

    @staticmethod
    def scoring(generation: Path, correct: dict[str, bool]) -> dict:
        return {"status": "completed_posthoc_after_freeze", "source_generation_sha256": sha(generation),
                "verdicts": [{"question_id": qid, "correct": value, "accepted": True, "resolved": True}
                             for qid, value in correct.items()]}

    def add_c_level(self, answer: str, correct: bool) -> tuple[Path, Path]:
        """A c_minimal generation that replaced bix-1-q2 with ``answer``, and its scoring."""
        c_generation = self.base / "tag_b_c_minimal_generation.json"
        c_generation.write_text(json.dumps({
            "level": "c_minimal", "protocol": {"lineage": ["c_minimal"], "model": "m", "reasoning_effort": "e"},
            "experiment_revision": "C-v6", "freeze": {"status": "frozen_before_private_scoring", "artifacts": []},
            "parent": {"parent_generation": str(self.b_generation), "parent_generation_sha256": sha(self.b_generation),
                       "parent_scoring_sha256": sha(self.b_scoring),
                       "locked_yes_question_ids": ["bix-1-q1", "bix-2-q1"], "rescue_question_ids": ["bix-1-q2"]},
            "rescue": [{"question_id": "bix-1-q2", "accepted": True, "fallback_to_primary": False,
                        "controller_status": "passed",
                        "final_result": {"capsule_uuid": "cap-1", "short_id": "bix-1", "workflow_summary": "c",
                                         "results": [item("bix-1-q2", answer, "c summary")]}}]}))
        c_scoring = self.base / "tag_b_c_minimal_scoring.json"
        c_scoring.write_text(json.dumps(self.scoring(c_generation, {
            "bix-1-q1": True, "bix-1-q2": correct, "bix-2-q1": True})))
        return c_generation, c_scoring

    def cleanup(self) -> None:
        self.tmp.cleanup()
        if self.created_runs and not any((ROOT / "runs").iterdir()):
            (ROOT / "runs").rmdir()


class ScriptedAgent:
    """Fake agent_call.run_agent: ``script(label, workspace)`` returns the capsule payload (or a dict with code)."""

    def __init__(self, script: Callable[[str, Path], Any]):
        self.script = script
        self.calls: list[dict] = []

    def __call__(self, prompt, schema, workspace, label, timeout):
        self.calls.append({"label": label, "prompt": prompt, "workspace": workspace,
                           "files": sorted(p.name for p in workspace.iterdir())})
        value = self.script(label, workspace)
        if isinstance(value, dict) and "code" in value:
            return agent_call.AgentCall(workspace, value["code"], [], "", 1.0)
        return agent_call.AgentCall(workspace, 0, [text(value)], "", 1.0)


@contextlib.contextmanager
def offline_h(agent: ScriptedAgent):
    """C's offline patches plus H's own workspace materialization and public rows."""
    with offline(agent, stage), patch.object(h_parent, "load_rows", lambda: [dict(r) for r in ROWS]), \
            patch.object(h_parent, "public_question", dict):
        yield


def run_main(variant, fixture: FrozenParent, *extra, generation: Path | None = None, scoring: Path | None = None,
             agent: ScriptedAgent | None = None, name: str = "h") -> dict:
    from conditions.h.core import rescue_main
    argv = ["runner.py", "--parent-generation", str(generation or fixture.b_generation),
            "--parent-scoring", str(scoring or fixture.b_scoring),
            "--parent-registry", str(fixture.base / f"{name}_registry.json"),
            "--grader", str(fixture.grader), "--feedback-judge", "surrogate",
            "--run-root", str(fixture.base / f"{name}_run"), "--output", str(fixture.base / f"{name}_generation.json"),
            "--report", str(fixture.base / f"{name}.md"), *extra]
    stdout = io.StringIO()
    with patch.object(sys, "argv", argv), offline_h(agent or ScriptedAgent(lambda label, ws: None)), \
            contextlib.redirect_stdout(stdout):
        rescue_main.main(variant)
    return json.loads(stdout.getvalue())


__all__ = ["ROOT", "ROWS", "KEY", "FrozenParent", "ScriptedAgent", "capsule_payload", "fake_prepare", "grader_runs",
           "item", "make_grader", "offline_h", "run_main", "sha"]
