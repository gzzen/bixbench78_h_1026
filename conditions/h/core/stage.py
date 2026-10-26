# Path: conditions/h/core/stage.py
# Purpose: One isolated OpenCode Yes/No repair stage for H heads (port of ngch inquiry_reflection.run_stage) and model setup.
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

import common.benchmark.a84_contract as substrate
from common.benchmark.a84_contract import NATIVE_SCHEMA, write_json
from common.integrity.capsule_workspace import prepare_workspace, validate_native_result
from common.integrity.codex_capture import command_metrics
from common.integrity.isolation_audit import audit_events_v121
from common.integrity.opencode_capture import generation_failure, write_result
from conditions.a import runner as a_runner
from conditions.b import runner as b_runner
from conditions.c.c_minimal import agent_call
from conditions.c.c_minimal.pipeline import localize_repair_paths


def configure(model: str, effort: str) -> None:
    """Point every runner H calls (fresh A/B heads and repair stages) at one model contract."""
    for module in (substrate, a_runner, b_runner, agent_call):
        module.MODEL = model
        module.REASONING_EFFORT = effort


def prompt_text(path: Path) -> str:
    """A head prompt without its leading `<!-- Path ... -->` header, i.e. the ngch source bytes."""
    text = path.read_text(encoding="utf-8")
    return text.split("\n", 1)[1] if text.startswith("<!--") else text


def fallback(rows: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    return {"capsule_uuid": rows[0]["capsule_uuid"], "short_id": rows[0]["short_id"], "workflow_summary": reason,
            "results": [{"question_id": row["question_id"], "status": "unresolved", "candidates": [],
                         "summary": reason} for row in rows]}


def payload(record: Mapping[str, Any], rows: list[dict[str, Any]], name: str = "capsule_result.json") -> dict[str, Any]:
    """A fresh A/B head's validated capsule result, or an all-unresolved fallback."""
    try:
        if not record.get("accepted"):
            raise ValueError(str(record.get("failure_reason", "controller rejected")))
        value = json.loads((Path(record["workspace"]) / name).read_text(encoding="utf-8"))
        validate_native_result(value, rows)
        return value
    except Exception as exc:
        return fallback(rows, f"failed closed: {type(exc).__name__}: {exc}")


def run_stage(
    *, head: str, stage: str, prior: dict[str, Any], feedback: list[dict[str, str]],
    rows: list[dict[str, Any]], root: Path, timeout: int, prompt_path: Path,
    extra_payloads: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reflect on No items while controller-locking every Yes item; any failure keeps ``prior``."""
    if all(item["feedback"] == "yes" for item in feedback):
        return prior, {"head": head, "stage": stage, "skipped": True, "accepted": True,
                       "runtime_seconds": 0.0, "total_agent_calls": 0}
    workspace, _ = prepare_workspace(stage, root / "heads" / head, rows)
    write_json(workspace / "prior_result.json", localize_repair_paths(copy.deepcopy(prior)))
    write_json(workspace / "feedback.json", {"items": feedback})
    for filename, value in (extra_payloads or {}).items():
        if Path(filename).name != filename or not filename.endswith(".json"):
            raise ValueError(f"unsafe extra payload name: {filename}")
        write_json(workspace / filename, value)
    prompt = b_runner.SOLVER_PROMPT.rstrip() + "\n\n" + prompt_text(prompt_path)
    call = agent_call.run_agent(prompt, NATIVE_SCHEMA, workspace, f"H/{head}/{stage}/{rows[0]['short_id']}", timeout)
    record: dict[str, Any] = {
        "head": head, "stage": stage, "workspace": str(workspace), "return_code": call.return_code,
        "runtime_seconds": call.runtime_seconds, "total_agent_calls": 1, "accepted": False,
        "retry_attempted": False, "usage": agent_call.captured_usage(call.events),
        "event_metrics": command_metrics(call.events),
    }
    final = copy.deepcopy(prior)
    if call.policy_violation is not None:
        record.update({"controller_status": "interrupted_policy_violation", "policy_violation": call.policy_violation,
                       "failure_reason": "generation safety policy violation: " + str(call.policy_violation.get("reason"))})
    else:
        try:
            if call.return_code != 0:
                raise RuntimeError(generation_failure(call.return_code, call.events))
            evidence = audit_events_v121(call.events, workspace)
            proposed = write_result(call.events, NATIVE_SCHEMA, workspace / "capsule_result.json")
            proposed, _ = b_runner.canonicalize_result_identity(proposed, rows)
            validate_native_result(proposed, rows)
            old = {item["question_id"]: item for item in prior["results"]}
            verdict = {item["question_id"]: item["feedback"] for item in feedback}
            for index, item in enumerate(proposed["results"]):
                if verdict[item["question_id"]] == "yes":
                    proposed["results"][index] = copy.deepcopy(old[item["question_id"]])
            validate_native_result(proposed, rows)
            final = proposed
            write_json(workspace / "analysis/path_audit.json", {"policy": "semantic resolved-path isolation",
                                                                "evidence": evidence})
            record.update({"accepted": True, "controller_status": "passed", "path_audit_evidence_count": len(evidence)})
        except Exception as exc:
            record.update({"controller_status": "failed", "failure_reason": f"{type(exc).__name__}: {exc}"})
    write_json(workspace / "head_canonical.json", final)
    write_json(workspace / "run_manifest.json", record)
    return final, record
