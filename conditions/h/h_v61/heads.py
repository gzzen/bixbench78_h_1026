# Path: conditions/h/h_v61/heads.py
# Purpose: The five D-v6.1 head families: three legacy B145 heads (fresh A/B + Yes/No repair) and two reflection heads.
"""Ports of ngch ``runner_b145.run_head`` and ``modules/multihead.run_reflection_head``.

Legacy heads start from a fresh, independent A or B capsule run and get their own
Yes/No from the feedback oracle before each repair stage.  Reflection heads start
from the frozen parent answer and the parent scoring's Yes/No, so they need no
oracle call.  Every repair stage locks the Yes results byte-for-byte.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from common.benchmark.a84_contract import write_json
from conditions.b import runner as b_runner
from conditions.c.c_minimal import agent_call
from conditions.h.core.feedback import FeedbackOracle
from conditions.h.core.stage import payload, run_stage

PROMPTS = Path(__file__).resolve().parent / "prompts"
CONSERVATIVE = PROMPTS / "b145_conservative.md"
AGGRESSIVE = PROMPTS / "b145_aggressive.md"
LEGACY_HEADS = ("full", "baseline_polling", "full_aggressive")
REFLECTION_PROMPTS = {
    "data_forensics": PROMPTS / "d4_data_forensics.md",
    "method_triangulation": PROMPTS / "d4_method_triangulation.md",
}


def fresh_initial(head: str, item: Mapping[str, Any], rows: list[dict[str, Any]], root: Path,
                  timeout: int) -> dict[str, Any]:
    """A fresh capsule run: A for baseline_polling, B (with its single policy retry) otherwise."""
    head_root = root / "heads" / head / "initial"
    if head == "baseline_polling":
        return b_runner.BASE_CONDITION.one(dict(item), rows, head_root, timeout)
    return b_runner.run_capsule(dict(item), rows, head_root, timeout)


def _usage(record: Mapping[str, Any]) -> dict[str, float]:
    timeline = Path(record.get("workspace", "")) / "event_timeline.json"
    if not record.get("workspace") or not timeline.is_file():
        return {}
    return agent_call.captured_usage(json.loads(timeline.read_text(encoding="utf-8")))


def run_legacy_head(head: str, item: Mapping[str, Any], rows: list[dict[str, Any]], root: Path,
                    timeout: int, oracle: FeedbackOracle) -> dict[str, Any]:
    record = fresh_initial(head, item, rows, root, timeout)
    current = payload(record, rows)
    if record.get("workspace") and Path(record["workspace"]).is_dir():
        write_json(Path(record["workspace"]) / "head_canonical.json", current)
    stages = [{**record, "head": head, "stage": "initial", "usage": _usage(record)}]
    label = f"{head}/{item['short_id']}"
    feedback = oracle.capsule_feedback(current, f"{label}/initial")
    transcript = [{"stage": "initial", "feedback": feedback}]
    stage_name = "poll_1" if head == "baseline_polling" else "conservative"
    current, repair = run_stage(head=head, stage=stage_name, prior=current, feedback=feedback, rows=rows,
                                root=root, timeout=timeout, prompt_path=CONSERVATIVE)
    stages.append(repair)
    if head == "full_aggressive":
        feedback = oracle.capsule_feedback(current, f"{label}/{stage_name}")
        transcript.append({"stage": stage_name, "feedback": feedback})
        current, repair = run_stage(head=head, stage="aggressive_1", prior=current, feedback=feedback, rows=rows,
                                    root=root, timeout=timeout, prompt_path=AGGRESSIVE)
        stages.append(repair)
    return {"head": head, "final": current, "stages": stages, "transcript": transcript}


def run_reflection_head(head: str, core: dict[str, Any], feedback: list[dict[str, str]],
                        rows: list[dict[str, Any]], root: Path, timeout: int) -> dict[str, Any]:
    """Each reflection head starts from the same frozen parent answer and the parent's Yes/No."""
    final, record = run_stage(head=head, stage="structural_reflection", prior=core, feedback=feedback, rows=rows,
                              root=root, timeout=timeout, prompt_path=REFLECTION_PROMPTS[head])
    return {"head": head, "final": final, "stages": [record],
            "transcript": [{"stage": "parent", "feedback": feedback}]}
