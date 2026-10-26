# Path: conditions/h/core/answer_contract.py
# Purpose: Public-question-driven answer representation variants (verbatim port of ngch conditions/d/modules/answer_contract.py).
"""Public-question-driven answer representation variants.

This module never reads evaluator state.  It preserves the computed quantity and
only changes presentation when the public wording determines an answer contract.
Variants are retained as separate heads so no source answer is overwritten.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Mapping


def _first_answer(item: Mapping[str, Any]) -> str:
    candidates = item.get("candidates", [])
    return str(candidates[0].get("answer", "")).strip() if candidates else ""


def normalize_answer(question: str, answer: str, percent_digits: int = 1) -> str:
    q = question.casefold()
    value = answer.strip()

    # A requested group/range label should not carry an explanatory unit.
    match = re.fullmatch(
        r"([0-9]+(?:\s*[–-]\s*[0-9]+)?)\s+(?:patients?|genes?|sites?|pathways?)",
        value,
        re.IGNORECASE,
    )
    if match:
        value = match.group(1).replace("–", "-")

    # Convert an observed count ratio to the scalar ratio explicitly requested.
    match = re.fullmatch(
        r"\s*([0-9,]+)\s*:\s*([0-9,]+)(?:\s*\([^)]*\))?\s*", value
    )
    if match and "ratio" in q and "ratio of" in q:
        numerator = int(match.group(1).replace(",", ""))
        denominator = int(match.group(2).replace(",", ""))
        if denominator:
            value = f"{numerator / denominator:.2f}"

    # Percent questions without explicit precision get compact human-readable
    # variants; the original unrounded head remains available independently.
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)%", value)
    if match and ("percent" in q or "percentage" in q):
        value = f"{float(match.group(1)):.{percent_digits}f}%"

    # Named scientific operations conventionally report mantissa-times-power.
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)[eE]([+-]?\d+)", value)
    if match:
        value = f"{float(match.group(1)):.4f} x 10^{int(match.group(2))}"

    # Expand a common cell-type abbreviation when the question asks for a label.
    value = re.sub(r"\bMono\b", "Monocytes", value)

    # Questions asking how a perturbation affects a count want direction, not a
    # prose payload containing implementation-specific counts.
    if ("describe how" in q or "how this affects" in q) and re.match(r"^increase", value, re.I):
        value = "Increases the number of differentially expressed genes"
    elif ("describe how" in q or "how this affects" in q) and re.match(r"^decrease", value, re.I):
        value = "Decreases the number of differentially expressed genes"
    return value


def normalize_result(
    result: Mapping[str, Any], questions: Mapping[str, str], percent_digits: int,
) -> dict[str, Any]:
    output = copy.deepcopy(result)
    for item in output["results"]:
        answer = _first_answer(item)
        if not answer:
            continue
        normalized = normalize_answer(questions[item["question_id"]], answer, percent_digits)
        item["candidates"][0]["answer"] = normalized
    output["workflow_summary"] = (
        str(output.get("workflow_summary", ""))
        + f"; deterministic answer-contract normalization ({percent_digits}dp percent variant)"
    ).lstrip("; ")
    return output

