# Path: conditions/h/core/feedback.py
# Purpose: Host-side per-question Yes/No for H, obtained from the sibling grader on frozen stage snapshots.
"""The H feedback oracle.

ngch's D called its private judge on the answer key inside generation.  This checkout
never opens the answer key, so every Yes/No H needs is produced the same way a final
export is scored: the answers are frozen into a predictions JSONL plus provenance
under ``<run_root>/feedback/``, ``../bixbench78_grader/score.py`` runs as a separate
process, and only its ``verdicts[].correct`` booleans are read back.  Attempt
containers mount only their own workspace, so ``feedback/`` is never solver-visible;
heads only ever receive ``{"question_id", "feedback": "yes"|"no"}`` items.

``feedback/ledger.jsonl`` keeps one verdict per (question, answer); a repeated or
resumed submission of the same answer is answered from it without grading again.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from conditions.h.core.aggregation import first_answer

ROOT = Path(__file__).resolve().parents[3]
GRADER = ROOT.parent / "bixbench78_grader/score.py"
JUDGES = ("deepseek", "surrogate")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def answer_key(qid: str, answer: str) -> str:
    return hashlib.sha256(json.dumps([qid, answer.strip()]).encode()).hexdigest()


class FeedbackOracle:
    def __init__(self, run_root: Path, grader: Path = GRADER, judge: str = "deepseek",
                 python: str = sys.executable):
        if judge not in JUDGES:
            raise ValueError(f"feedback judge must be one of {JUDGES}")
        self.root = run_root / "feedback"
        self.grader, self.judge, self.python = grader, judge, python
        self.ledger_path = self.root / "ledger.jsonl"
        self._lock = threading.Lock()
        self.verdicts: dict[str, bool] = {}
        self.calls = 0
        if self.ledger_path.is_file():
            for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    self.verdicts[item["key"]] = item["correct"]
        if self.root.is_dir():
            # Numbering continues after every earlier grading directory, finished or not.
            self.calls = sum(1 for path in self.root.iterdir() if path.is_dir())

    def manifest(self) -> dict[str, Any]:
        return {"grader": str(self.grader), "judge": self.judge,
                "grader_sha256": sha256(self.grader) if self.grader.is_file() else None,
                "payload": "per-question yes/no only; ideal answers never read in-process"}

    def judge_many(self, items: Iterable[tuple[str, str]], label: str) -> dict[tuple[str, str], bool]:
        """Verdict for each (question_id, answer); an empty answer is No without grading."""
        wanted = list(dict.fromkeys((qid, answer.strip()) for qid, answer in items))
        with self._lock:
            pending = [item for item in wanted if item[1] and answer_key(*item) not in self.verdicts]
            # score.py refuses duplicate question IDs, so one grader run holds each question once.
            while pending:
                batch, seen, rest = [], set(), []
                for qid, answer in pending:
                    (rest if qid in seen else batch).append((qid, answer))
                    seen.add(qid)
                self._grade(batch, label)
                pending = rest
        return {(qid, answer): bool(answer) and self.verdicts[answer_key(qid, answer)] for qid, answer in wanted}

    def _grade(self, batch: Sequence[tuple[str, str]], label: str) -> None:
        self.calls += 1
        directory = self.root / f"{self.calls:05d}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', label)[:80]}"
        directory.mkdir(parents=True)
        predictions, provenance = directory / "predictions.jsonl", directory / "provenance.json"
        predictions.write_text("".join(json.dumps({
            "condition": "h_feedback", "question_id": qid, "status": "resolved",
            "candidates": [{"answer": answer, "method": "H stage snapshot", "evidence": label}],
            "controller_accepted": True,
        }, ensure_ascii=False) + "\n" for qid, answer in batch), encoding="utf-8")
        provenance.write_text(json.dumps({
            "condition": "h_feedback", "source_run": label, "questions": len(batch),
            "freeze_status": "frozen_before_private_scoring", "generation_scorer_or_evaluator": False,
            "predictions_sha256": sha256(predictions),
        }, indent=2) + "\n", encoding="utf-8")
        output = directory / "scoring.json"
        process = subprocess.run(
            [self.python, str(self.grader), "--predictions", str(predictions), "--provenance", str(provenance),
             "--output", str(output), "--judge", self.judge],
            cwd=self.grader.parent, capture_output=True, text=True)
        if process.returncode != 0:
            raise RuntimeError(f"feedback grader failed ({label}): {process.stderr.strip()[-500:]}")
        verdicts = {item["question_id"]: bool(item["correct"])
                    for item in json.loads(output.read_text(encoding="utf-8"))["verdicts"]}
        with self.ledger_path.open("a", encoding="utf-8") as ledger:
            for qid, answer in batch:
                key = answer_key(qid, answer)
                self.verdicts[key] = verdicts[qid]
                ledger.write(json.dumps({"key": key, "question_id": qid, "correct": verdicts[qid],
                                         "grading": directory.name}) + "\n")

    def submit(self, qid: str, answer: str, label: str) -> bool:
        return self.judge_many([(qid, answer)], label)[(qid, answer.strip())]

    def capsule_feedback(self, value: Mapping[str, Any], label: str) -> list[dict[str, str]]:
        """ngch ``feedback_for``: one Yes/No per result, judged on its first candidate."""
        answers = [(item["question_id"], first_answer(item)) for item in value["results"]]
        verdicts = self.judge_many(answers, label)
        return [{"question_id": qid, "feedback": "yes" if verdicts[(qid, answer)] else "no"}
                for qid, answer in answers]
