# Path: conditions/h/core/parent.py
# Purpose: Load and verify H's frozen parent (B, or a C level over B) and its post-freeze scoring; Yes lock and targets.
"""H never calls the B or C runner for its parent.

The parent is one frozen generation (B, or a C level) plus the post-freeze scoring of
its export.  H reads the exported rows (the exact answers that were scored), locks
every question the scoring marks correct, accepted and resolved, and treats every
other question as a target.  Only the per-question booleans are read from scoring.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.benchmark.a84_contract import load_rows, public_question, write_json
from common.integrity.freeze import frozen_workspace
from scripts.export_predictions import c_rows, export_rows, load_frozen

ROOT = Path(__file__).resolve().parents[3]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _resolve(path: str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def is_locked(verdict: dict) -> bool:
    return bool(verdict["correct"] and verdict.get("accepted") and verdict.get("resolved"))


def unresolved_item(qid: str, reason: str) -> dict[str, Any]:
    return {"question_id": qid, "status": "unresolved", "candidates": [], "summary": reason}


@dataclass
class HParent:
    """Everything H needs from its frozen parent; ``registry`` is the serialisable summary."""

    registry: dict[str, Any]
    rows: dict[str, dict[str, Any]]                 # qid -> public question row
    core: dict[str, dict[str, Any]]                 # qid -> scored parent result item
    capsules: list[dict[str, Any]]                  # {short_id, capsule_uuid, question_ids} in B order
    workspaces: dict[str, Path]                     # capsule_uuid -> frozen B workspace (read only)

    @property
    def locked(self) -> list[str]:
        return self.registry["locked_question_ids"]

    @property
    def targets(self) -> list[str]:
        return self.registry["target_question_ids"]

    def capsule_rows(self, capsule: dict[str, Any]) -> list[dict[str, Any]]:
        return [dict(self.rows[qid]) for qid in capsule["question_ids"]]

    def capsule_payload(self, capsule: dict[str, Any]) -> dict[str, Any]:
        """The parent answers of one capsule in the B capsule-result shape."""
        return {"capsule_uuid": capsule["capsule_uuid"], "short_id": capsule["short_id"],
                "workflow_summary": f"frozen H parent ({self.registry['parent_kind']})",
                "results": [copy.deepcopy(self.core[qid]) for qid in capsule["question_ids"]]}

    def capsule_feedback(self, capsule: dict[str, Any]) -> list[dict[str, str]]:
        """Parent Yes/No: Yes exactly for the locked (correct, accepted, resolved) questions."""
        locked = set(self.locked)
        return [{"question_id": qid, "feedback": "yes" if qid in locked else "no"}
                for qid in capsule["question_ids"]]

    def triggered(self) -> list[dict[str, Any]]:
        targets = set(self.targets)
        return [capsule for capsule in self.capsules if targets & set(capsule["question_ids"])]


def _b_items(generation: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Path]]:
    items: dict[str, dict[str, Any]] = {}
    workspaces: dict[str, Path] = {}
    for episode in generation["episodes"]:
        workspace = frozen_workspace(episode["workspace"], ROOT)
        workspaces[episode["capsule_uuid"]] = workspace
        path = workspace / "capsule_result.json"
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"results": []}
        by_id = {item.get("question_id"): item for item in payload.get("results", []) if isinstance(item, dict)}
        for qid in episode["question_ids"]:
            items[qid] = copy.deepcopy(by_id.get(qid) or unresolved_item(qid, "no frozen B result"))
    return items, workspaces


def load_h_parent(generation_path: Path, scoring_path: Path, registry_path: Path | None) -> HParent:
    """Verify the frozen parent and its scoring; write or check the shared H parent registry."""
    generation = load_frozen(generation_path)
    scoring = json.loads(scoring_path.read_text(encoding="utf-8"))
    if scoring.get("status") != "completed_posthoc_after_freeze":
        raise RuntimeError("parent scoring is not complete")
    if "source_generation_sha256" in scoring and scoring["source_generation_sha256"] != digest(generation_path):
        raise RuntimeError("parent scoring was made for a different parent generation")
    kind = "c" if "rescue" in generation else "b"
    if kind == "c":
        rows, _ = c_rows(generation, "h_parent")
        b_path = _resolve(generation["parent"]["parent_generation"])
        b_generation = load_frozen(b_path)
        replaced = {item["question_id"]: item for item in generation["rescue"]}
    else:
        rows = export_rows(generation, "b", "h_parent")
        b_path, b_generation, replaced = generation_path, generation, {}
    items, workspaces = _b_items(b_generation)
    core: dict[str, dict[str, Any]] = {}
    for row in rows:
        qid = row["question_id"]
        item = items[qid]
        if row.get("c_replaced"):
            item = next(x for x in replaced[qid]["final_result"]["results"] if x["question_id"] == qid)
        core[qid] = {"question_id": qid, "status": row["status"], "candidates": copy.deepcopy(row["candidates"]),
                     "summary": str(item.get("summary") or "frozen parent result")}
    verdicts = {item["question_id"]: item for item in scoring["verdicts"]}
    if len(verdicts) != len(scoring["verdicts"]) or sorted(verdicts) != sorted(core):
        raise RuntimeError("parent scoring question IDs do not match the frozen parent generation")
    public = {row["question_id"]: public_question(row) for row in load_rows()}
    capsules = [{"short_id": e["short_id"], "capsule_uuid": e["capsule_uuid"], "question_ids": list(e["question_ids"])}
                for e in b_generation["episodes"]]
    order = [qid for capsule in capsules for qid in capsule["question_ids"]]
    locked = [qid for qid in order if is_locked(verdicts[qid])]
    registry = {
        "status": "frozen_h_parent",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "parent_kind": kind,
        "c_level": generation.get("level") if kind == "c" else None,
        "parent_generation": _relative(generation_path),
        "parent_generation_sha256": digest(generation_path),
        "parent_scoring": _relative(scoring_path),
        "parent_scoring_sha256": digest(scoring_path),
        "b_generation": _relative(b_path),
        "b_generation_sha256": digest(b_path),
        "b_model": b_generation.get("manifest", {}).get("model"),
        "b_reasoning_effort": b_generation.get("manifest", {}).get("reasoning_effort"),
        "question_count": len(order),
        "locked_question_ids": locked,
        "target_question_ids": [qid for qid in order if qid not in set(locked)],
        "locked_rule": "parent evaluator Yes (correct, accepted, resolved) is immutable; H cannot rewrite it",
        "target_rule": "every other question; heads run on each capsule that holds a target",
    }
    if registry_path is not None:
        if registry_path.exists():
            existing = json.loads(registry_path.read_text(encoding="utf-8"))
            comparable, expected = copy.deepcopy(existing), copy.deepcopy(registry)
            comparable.pop("created_at", None)
            expected.pop("created_at", None)
            if comparable != expected:
                raise RuntimeError("frozen H parent registry mismatch")
            registry = existing
        else:
            write_json(registry_path, registry)
    return HParent(registry, {qid: public[qid] for qid in order}, core, capsules, workspaces)
