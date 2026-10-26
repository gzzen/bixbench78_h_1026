# Path: conditions/h/core/aggregation.py
# Purpose: Deterministic reference-free, sequential Yes/No and oracle-union aggregation of H heads (port of ngch conditions/d/exploration/aggregation.py).
from __future__ import annotations

import copy
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence


def first_answer(result: Mapping[str, Any]) -> str:
    candidates = result.get("candidates", [])
    return str(candidates[0].get("answer", "")).strip() if candidates else ""


def _normalized(answer: str) -> str:
    value = re.sub(r"\s+", " ", answer).strip().casefold()
    # Collapse purely numeric spelling variants (for example 13, 13.0, 1.3e1)
    # without conflating percentages, units, ratios, or structured answers.
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?", value):
        try:
            number = Decimal(value)
            if number.is_finite():
                return f"numeric:{number.normalize()}"
        except InvalidOperation:
            pass
    return value


def sequential_yes_no_select(
    core: Mapping[str, Mapping[str, Any]],
    heads: Mapping[str, Mapping[str, Mapping[str, Any]]],
    core_verdicts: Mapping[str, bool],
    head_verdicts: Mapping[str, Mapping[str, bool]],
    head_order: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Submit unique head answers in order and stop at the first Yes verdict.

    The parent answer is submission one.  Each head contributes its primary answer;
    equivalent answers are submitted only once.  Verdicts contain only Yes/No
    feedback, never reference-answer text.
    """
    final = {qid: copy.deepcopy(result) for qid, result in core.items()}
    decisions: list[dict[str, Any]] = []
    for qid, core_result in core.items():
        attempts: list[dict[str, Any]] = []
        seen: set[str] = set()
        core_answer = first_answer(core_result)
        if core_answer:
            seen.add(_normalized(core_answer))
            attempts.append({"source": "parent", "answer": core_answer,
                             "verdict": "yes" if core_verdicts.get(qid, False) else "no"})
        accepted_source = "parent" if core_verdicts.get(qid, False) else None
        if accepted_source is None:
            for name in head_order:
                candidate = heads.get(name, {}).get(qid)
                answer = first_answer(candidate or {})
                if not answer:
                    continue
                normalized = _normalized(answer)
                if normalized in seen:
                    continue
                seen.add(normalized)
                correct = bool(head_verdicts.get(name, {}).get(qid, False))
                attempts.append({"source": name, "answer": answer,
                                 "verdict": "yes" if correct else "no"})
                if correct:
                    final[qid] = copy.deepcopy(candidate)
                    accepted_source = name
                    break
        decisions.append({
            "question_id": qid,
            "accepted": accepted_source is not None,
            "accepted_source": accepted_source,
            "submission_count": len(attempts),
            "unique_candidates_considered": len(seen),
            "attempts": attempts,
        })
    return final, decisions


def reference_free_select(
    core: Mapping[str, Mapping[str, Any]],
    heads: Mapping[str, Mapping[str, Mapping[str, Any]]],
    targets: set[str],
    head_order: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Preserve the parent off-target; on-target prefer head consensus, then fixed priority."""
    final = {qid: copy.deepcopy(result) for qid, result in core.items()}
    decisions: list[dict[str, Any]] = []
    for qid in core:
        if qid not in targets:
            decisions.append({"question_id": qid, "source": "parent", "rule": "not_triggered"})
            continue
        available = []
        for name in head_order:
            candidate = heads.get(name, {}).get(qid)
            answer = first_answer(candidate or {})
            if answer:
                available.append((name, candidate, _normalized(answer)))
        counts = Counter(item[2] for item in available)
        consensus = {answer for answer, count in counts.items() if count >= 2}
        selected = next((item for item in available if item[2] in consensus), None)
        rule = "head_consensus" if selected else "fixed_priority"
        if selected is not None:
            final[qid] = copy.deepcopy(selected[1])
            source = selected[0]
        elif available:
            final[qid] = copy.deepcopy(available[0][1])
            source = available[0][0]
        else:
            source = "parent"
            rule = "all_heads_unresolved"
        decisions.append({"question_id": qid, "source": source, "rule": rule})
    return final, decisions


def oracle_select(
    core: Mapping[str, Mapping[str, Any]],
    heads: Mapping[str, Mapping[str, Mapping[str, Any]]],
    verdicts: Mapping[str, Mapping[str, bool]],
    targets: set[str],
    head_order: Sequence[str],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Oracle candidate coverage endpoint; verdicts are private booleans, never answer text."""
    final = {qid: copy.deepcopy(result) for qid, result in core.items()}
    decisions: list[dict[str, Any]] = []
    for qid in core:
        source = "parent"
        if qid in targets:
            for name in head_order:
                if verdicts.get(name, {}).get(qid, False):
                    final[qid] = copy.deepcopy(heads[name][qid])
                    source = name
                    break
        decisions.append({"question_id": qid, "source": source})
    return final, decisions


def capsule_results(
    episodes: Sequence[Mapping[str, Any]], selected: Mapping[str, Mapping[str, Any]], label: str
) -> list[dict[str, Any]]:
    payloads = []
    for episode in episodes:
        core = episode["final_result"]
        payloads.append({
            "capsule_uuid": core["capsule_uuid"],
            "short_id": core["short_id"],
            "workflow_summary": label,
            "results": [copy.deepcopy(selected[item["question_id"]]) for item in core["results"]],
        })
    return payloads
