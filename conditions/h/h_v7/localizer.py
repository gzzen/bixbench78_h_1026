# Path: conditions/h/h_v7/localizer.py
# Purpose: Capsule-level answer-blind error localization and repair routing for D-v7 (port of ngch conditions/d/modules/v7_localizer.py; only imports changed).
"""Capsule-level, answer-blind error localization and repair routing for D-v7."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from conditions.h.h_v7.contracts import protocol_is_named


GROUNDING_KEYS = (
    "data_object", "observation_unit", "denominator_or_population",
    "comparison", "metric_or_outcome", "method_or_estimator",
)


def _grounding(workspace: Path) -> dict[str, dict[str, Any]]:
    path = workspace / "analysis/grounding.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {
            item["question_id"]: item
            for item in value.get("questions", [])
            if isinstance(item, dict) and isinstance(item.get("question_id"), str)
        }
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _signature(value: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(value.get(key, "unresolved")).strip().casefold() for key in GROUNDING_KEYS[:3])


def _inventory(workspace: Path) -> dict[str, list[str]]:
    analysis = workspace / "analysis"
    scripts = []
    intermediate = []
    if analysis.is_dir():
        for path in sorted(analysis.rglob("*")):
            if not path.is_file():
                continue
            relative = str(path.relative_to(workspace))
            if relative.startswith("analysis/scripts/"):
                scripts.append(relative)
            else:
                intermediate.append(relative)
    return {"scripts": scripts, "intermediate_results": intermediate}


def localize_capsule(
    *, rows: Sequence[Mapping[str, Any]], feedback: Sequence[Mapping[str, str]],
    workspace: Path, protocol_spec: Mapping[str, Any], answer_contract: Mapping[str, Any],
    sibling_anchors: bool = True,
) -> dict[str, Any]:
    """Classify failure topology using public metadata and Yes/No structure."""
    verdict = {item["question_id"]: item["feedback"] for item in feedback}
    yes = [row["question_id"] for row in rows if verdict.get(row["question_id"]) == "yes"]
    no = [row["question_id"] for row in rows if verdict.get(row["question_id"]) != "yes"]
    grounding = _grounding(workspace)
    by_signature: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for row in rows:
        by_signature[_signature(grounding.get(row["question_id"], {}))].append(row["question_id"])

    no_ratio = len(no) / max(1, len(rows))
    no_clusters = [
        [qid for qid in values if qid in no]
        for values in by_signature.values()
    ]
    related_cluster = max(no_clusters, key=len, default=[])
    if not no:
        pattern = "all_questions_yes"
        confidence = "high"
    elif no_ratio >= 0.8:
        pattern = "capsule_wide_failure"
        confidence = "high"
    elif len(no) == 1:
        pattern = "isolated_question_failure"
        confidence = "high"
    elif len(related_cluster) >= 2 and len(related_cluster) < len(no):
        pattern = "related_subset_failure"
        confidence = "medium"
    elif len(related_cluster) == len(no) and len(no) >= 2:
        pattern = "related_subset_failure"
        confidence = "high"
    else:
        pattern = "mixed_question_failures"
        confidence = "low"

    diagnostics: dict[str, Any] = {}
    capsule_heads: set[str] = set()
    suspected_layers: set[str] = set()
    failure_tags: set[str] = {pattern}
    for row in rows:
        qid = row["question_id"]
        if qid not in no:
            continue
        protocol = protocol_spec["questions"].get(qid, {})
        contract = answer_contract["questions"].get(qid, {})
        sig = _signature(grounding.get(qid, {}))
        shared_yes = [candidate for candidate in by_signature.get(sig, []) if candidate in yes]
        likely: list[str] = []
        repairs: list[str] = []
        if contract.get("explicit_precision") or contract.get("answer_type") in {
            "integer", "percentage", "ratio", "category", "direction",
        }:
            likely.append("contract_like_failure")
            repairs.append("answer_contract_repair")
            failure_tags.add("contract_like_failure")
            suspected_layers.add("downstream_answer_contract")
        if protocol_is_named(protocol):
            likely.append("protocol_specific_failure")
            repairs.append("protocol_fidelity")
            failure_tags.add("protocol_specific_failure")
            capsule_heads.add("protocol_fidelity")
            suspected_layers.add("named_protocol_execution")
        if pattern == "capsule_wide_failure":
            likely.extend(("shared_upstream_pipeline", "grounding"))
            repairs.extend(("full_recompute", "data_forensics", "alternative_grounding"))
            capsule_heads.update(("full_recompute", "data_forensics", "alternative_grounding"))
            suspected_layers.update(("data_object", "sample_scope", "filtering", "join", "grounding"))
        elif pattern == "related_subset_failure":
            likely.append("shared_derived_branch")
            repairs.extend(("data_forensics", "method_triangulation"))
            capsule_heads.update(("data_forensics", "method_triangulation"))
            suspected_layers.update(("derived_table", "branch_filtering", "method"))
        elif pattern == "isolated_question_failure":
            likely.append("question_specific_calculation")
            repairs.extend(("method_triangulation", "simple_baseline"))
            capsule_heads.update(("method_triangulation", "simple_baseline"))
            suspected_layers.update(("statistic", "denominator", "contrast", "unit"))
        else:
            repairs.extend(("simple_baseline", "method_triangulation", "alternative_grounding"))
            capsule_heads.update(("simple_baseline", "method_triangulation", "alternative_grounding"))
            suspected_layers.update(("method", "grounding"))
        if sibling_anchors and shared_yes:
            likely.append("shared_steps_validated_by_sibling_yes")
        diagnostics[qid] = {
            "likely_failure_types": list(dict.fromkeys(likely)),
            "shared_with_yes_questions": shared_yes if sibling_anchors else [],
            "validated_by_sibling_yes": shared_yes if sibling_anchors else [],
            "recommended_repairs": list(dict.fromkeys(repairs)),
            "grounding_signature": dict(zip(GROUNDING_KEYS[:3], sig)),
        }

    if not capsule_heads and no:
        capsule_heads.add("method_triangulation")
    return {
        "capsule_id": rows[0]["capsule_uuid"],
        "yes_questions": yes,
        "no_questions": no,
        "failure_pattern": pattern,
        "failure_tags": sorted(failure_tags),
        "shared_pipeline_confidence": confidence,
        "suspected_failure_layers": sorted(suspected_layers),
        "question_diagnostics": diagnostics,
        "recommended_heads": sorted(capsule_heads),
        "dependency_groups": [
            {"grounding_signature": list(signature), "question_ids": values}
            for signature, values in by_signature.items()
        ],
        "b_artifact_inventory": _inventory(workspace),
        "policy": "controller-localized from public metadata and per-question Yes/No only",
    }
