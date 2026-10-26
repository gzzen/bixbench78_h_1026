#!/usr/bin/env python3
# Path: scripts/export_predictions.py
# Purpose: Export one frozen Native, B, C or H generation into normalized prediction JSONL.
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.integrity.freeze import frozen_workspace  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def unresolved(question_id: str) -> dict[str, Any]:
    return {"question_id": question_id, "status": "unresolved", "candidates": []}



def load_frozen(path: Path) -> dict[str, Any]:
    generation = load_json(path)
    if generation.get("freeze", {}).get("status") != "frozen_before_private_scoring":
        raise RuntimeError("generation is not frozen before private scoring")
    changed = [artifact["path"] for artifact in generation["freeze"].get("artifacts", [])
               if artifact.get("sha256") and (not (ROOT / artifact["path"]).is_file()
                                              or sha256(ROOT / artifact["path"]) != artifact["sha256"])]
    if changed:
        raise RuntimeError(f"frozen artifact changed: {changed[:3]}")
    return generation


def export_rows(generation: dict[str, Any], condition: str, source_run: str) -> list[dict[str, Any]]:
    records = generation["records" if condition == "native" else "episodes"]
    rows: list[dict[str, Any]] = []
    for record in records:
        workspace = frozen_workspace(record["workspace"], ROOT)
        local_failures = record.get("question_local_failures") or {}
        result_path = workspace / ("question_result.json" if condition == "native" else "capsule_result.json")
        if record.get("accepted") and not result_path.is_file():
            raise RuntimeError(f"accepted {condition} record has no frozen result: {result_path}")
        if condition == "native":
            question_ids = [record["question_id"]]
            payload = load_json(result_path) if result_path.is_file() else unresolved(question_ids[0])
            results = [payload]
        else:
            question_ids = list(record["question_ids"])
            payload = load_json(result_path) if result_path.is_file() else {"results": []}
            by_id = {
                item.get("question_id"): item
                for item in payload.get("results", [])
                if isinstance(item, dict) and isinstance(item.get("question_id"), str)
            }
            results = [by_id.get(question_id, unresolved(question_id)) for question_id in question_ids]
        for question_id, result in zip(question_ids, results, strict=True):
            candidates = result.get("candidates") if isinstance(result.get("candidates"), list) else []
            status = result.get("status") if result.get("status") in {"resolved", "unresolved"} else "unresolved"
            accepted = bool(record.get("accepted")) and question_id not in local_failures
            rows.append({
                "condition": condition,
                "question_id": question_id,
                "short_id": record["short_id"],
                "capsule_uuid": record["capsule_uuid"],
                "status": status,
                "candidates": candidates,
                "controller_accepted": accepted,
                "controller_status": record.get("controller_status"),
                "question_local_failure": local_failures.get(question_id) if isinstance(local_failures, dict) else None,
                "return_code": record.get("return_code"),
                "runtime_seconds": record.get("runtime_seconds"),
                "source_run": source_run,
            })
    return rows


def c_rows(c_generation: dict[str, Any], source_run: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Frozen B rows for locked questions; a C answer only where the repair was accepted and not a fallback.

    Rescue questions without a validated C answer keep their B row with
    controller_accepted=false, so compliant scoring of this file equals the
    historical "B compliant correct + C rescue successes" (frozen_b3_scorer.py).
    """
    parent = c_generation["parent"]
    parent_path = Path(parent["parent_generation"])
    parent_path = parent_path if parent_path.is_absolute() else ROOT / parent_path
    if sha256(parent_path) != parent["parent_generation_sha256"]:
        raise RuntimeError("frozen B parent generation hash mismatch")
    parent_generation = load_frozen(parent_path)
    rows = export_rows(parent_generation, "b", source_run)
    locked = set(parent["locked_yes_question_ids"])
    rescue = {item["question_id"]: item for item in c_generation["rescue"]}
    if locked & set(rescue) or not set(rescue) <= set(parent["rescue_question_ids"]):
        raise RuntimeError("C generation rescues a locked question or an unknown question")
    replaced = 0
    for row in rows:
        row.update(condition="c", c_level=c_generation.get("level"),
                   b_locked=row["question_id"] in locked, c_replaced=False)
        repair = rescue.get(row["question_id"])
        if row["b_locked"] or repair is None:
            continue
        row["controller_accepted"] = False
        row["c_controller_status"] = repair.get("controller_status")
        if not repair.get("accepted") or repair.get("fallback_to_primary"):
            continue
        results = [item for item in repair.get("final_result", {}).get("results", [])
                   if item.get("question_id") == row["question_id"]]
        if not results:
            continue
        candidates = results[0].get("candidates") if isinstance(results[0].get("candidates"), list) else []
        status = results[0].get("status") if results[0].get("status") in {"resolved", "unresolved"} else "unresolved"
        row.update(status=status, candidates=candidates, controller_accepted=True,
                   controller_status=repair.get("controller_status"), question_local_failure=None,
                   return_code=repair.get("return_code"), runtime_seconds=repair.get("runtime_seconds"),
                   c_replaced=True)
        replaced += 1
    protocol = c_generation.get("protocol", {})
    extra = {"parent_generation": str(parent_path), "parent_generation_sha256": parent["parent_generation_sha256"],
             "parent_scoring_sha256": parent["parent_scoring_sha256"], "c_level": c_generation.get("level"),
             "c_lineage": protocol.get("lineage"), "experiment_revision": c_generation.get("experiment_revision"),
             "locked_b_questions": len(locked), "c_rescue_targets": len(rescue), "c_replaced_answers": replaced,
             "c_repair_model": protocol.get("model"), "c_repair_reasoning_effort": protocol.get("reasoning_effort"),
             "model": parent_generation["manifest"].get("model"),
             "reasoning_effort": parent_generation["manifest"].get("reasoning_effort"),
             "source_manifest_sha256": parent_generation["freeze"].get("manifest_sha256")}
    return rows, extra


def h_rows(h_generation: dict[str, Any], source_run: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The scored parent rows (B, or a C level over B); an H answer only where H replaced a target.

    Locked parent questions are byte-identical to the parent export.  A replaced target
    gets the selected head result with controller_accepted=true; every other target keeps
    its parent row, so compliant scoring of this file equals parent correct + H rescues.
    """
    parent = h_generation["parent"]
    parent_path = Path(parent["parent_generation"])
    parent_path = parent_path if parent_path.is_absolute() else ROOT / parent_path
    if sha256(parent_path) != parent["parent_generation_sha256"]:
        raise RuntimeError("frozen H parent generation hash mismatch")
    parent_generation = load_frozen(parent_path)
    if parent["parent_kind"] == "c":
        rows, parent_extra = c_rows(parent_generation, source_run)
    else:
        rows, parent_extra = export_rows(parent_generation, "b", source_run), {}
    locked = set(parent["locked_question_ids"])
    replaced = h_generation["h_replaced"]
    if locked & set(replaced) or not set(replaced) <= set(parent["target_question_ids"]):
        raise RuntimeError("H generation replaces a locked question or an unknown question")
    for row in rows:
        qid = row["question_id"]
        row.update(condition="h", h_variant=h_generation.get("variant"), c_level=parent.get("c_level"),
                   parent_locked=qid in locked, h_replaced=qid in replaced)
        if qid not in replaced:
            continue
        item = h_generation["final_results"][qid]
        candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else []
        status = item.get("status") if item.get("status") in {"resolved", "unresolved"} else "unresolved"
        row.update(status=status, candidates=candidates, controller_accepted=True, controller_status="h_selected",
                   question_local_failure=None, return_code=None, runtime_seconds=None,
                   h_source=replaced[qid]["source"], h_rule=replaced[qid]["rule"])
    protocol = h_generation.get("protocol", {})
    summary = h_generation.get("generation_summary", {})
    extra = {"parent_generation": str(parent_path), "parent_generation_sha256": parent["parent_generation_sha256"],
             "parent_scoring_sha256": parent["parent_scoring_sha256"], "parent_kind": parent["parent_kind"],
             "c_level": parent.get("c_level"), "h_variant": h_generation.get("variant"),
             "experiment_revision": h_generation.get("experiment_revision"),
             "locked_parent_questions": len(locked), "h_targets": len(parent["target_question_ids"]),
             "h_replaced_answers": len(replaced),
             "h_model": protocol.get("model"), "h_reasoning_effort": protocol.get("reasoning_effort"),
             "feedback_assisted": True, "feedback_oracle_calls": summary.get("feedback_oracle_calls"),
             "model": parent_extra.get("model", parent_generation.get("manifest", {}).get("model")),
             "reasoning_effort": parent_extra.get("reasoning_effort",
                                                  parent_generation.get("manifest", {}).get("reasoning_effort")),
             "source_manifest_sha256": parent_extra.get("source_manifest_sha256",
                                                        parent_generation["freeze"].get("manifest_sha256"))}
    return rows, extra


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", type=Path, required=True)
    parser.add_argument("--condition", choices=("native", "b", "c", "h"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--source-run", required=True)
    args = parser.parse_args()

    generation = load_frozen(args.generation)
    if args.condition == "c":
        rows, extra = c_rows(generation, args.source_run)
    elif args.condition == "h":
        rows, extra = h_rows(generation, args.source_run)
    else:
        rows = export_rows(generation, args.condition, args.source_run)
        extra = {"source_manifest_sha256": generation["freeze"].get("manifest_sha256"),
                 "model": generation["manifest"].get("model"),
                 "reasoning_effort": generation["manifest"].get("reasoning_effort")}

    if len(rows) != 78 or len({row["question_id"] for row in rows}) != 78:
        raise RuntimeError("expected exactly 78 unique exported questions")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    provenance = {
        "condition": args.condition,
        "source_run": args.source_run,
        "source_generation": str(args.generation.resolve()),
        "source_generation_sha256": sha256(args.generation),
        "questions": 78,
        **extra,
        "generation_scorer_or_evaluator": False,
        "freeze_status": generation["freeze"]["status"],
        "predictions_sha256": sha256(args.output),
    }
    args.provenance.parent.mkdir(parents=True, exist_ok=True)
    args.provenance.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"condition": args.condition, "questions": len(rows), "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
