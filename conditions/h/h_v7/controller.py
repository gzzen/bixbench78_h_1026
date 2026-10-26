# Path: conditions/h/h_v7/controller.py
# Purpose: Candidate registry, metadata, disagreement analysis and ranking for D-v7 (port of ngch conditions/d/modules/v7_controller.py; only imports changed).
"""Candidate registry, metadata, disagreement analysis, and ranking for D-v7."""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from conditions.h.core.aggregation import _normalized, first_answer


METADATA_FIELDS = (
    "question_id", "answer", "source_head", "data_object", "sample_scope",
    "observation_unit", "denominator", "comparison", "method", "package",
    "version", "unit", "answer_type", "confidence", "scientific_hypothesis",
    "evidence",
    "shared_upstream_dependencies", "independent_grounding", "independent_implementation",
)


def candidate_result(item: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(dict(item))
    value["status"] = "resolved"
    value["candidates"] = [copy.deepcopy(dict(candidate))]
    return value


def fallback_metadata(
    *, qid: str, answer: str, head: str, result: Mapping[str, Any],
    grounding: Mapping[str, Any], contract: Mapping[str, Any], hypothesis: str,
) -> dict[str, Any]:
    candidate = result.get("candidates", [{}])[0] if result.get("candidates") else {}
    return {
        "question_id": qid,
        "answer": answer,
        "source_head": head,
        "data_object": str(grounding.get("data_object", "unresolved")),
        "sample_scope": str(grounding.get("denominator_or_population", "unresolved")),
        "observation_unit": str(grounding.get("observation_unit", "unresolved")),
        "denominator": str(grounding.get("denominator_or_population", "unresolved")),
        "comparison": str(grounding.get("comparison", "unresolved")),
        "method": str(candidate.get("method", grounding.get("method_or_estimator", "unresolved"))),
        "package": "unresolved",
        "version": "unresolved",
        "unit": str(contract.get("unit") or grounding.get("unit_or_scale", "unresolved")),
        "answer_type": str(contract.get("answer_type", "unresolved")),
        "confidence": "low",
        "scientific_hypothesis": hypothesis,
        "evidence": str(candidate.get("evidence", "controller fallback metadata")),
        "metadata_status": "controller_fallback",
    }


def load_candidate_metadata(workspace: Path | None) -> dict[str, dict[str, Any]]:
    if workspace is None:
        return {}
    path = workspace / "analysis/candidate_metadata.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        rows = value.get("questions", value) if isinstance(value, dict) else value
        if not isinstance(rows, list):
            return {}
        output = {}
        for item in rows:
            if not isinstance(item, dict) or not isinstance(item.get("question_id"), str):
                continue
            normalized = {field: item.get(field, "unresolved") for field in METADATA_FIELDS}
            normalized["metadata_status"] = "head_supplied"
            output[item["question_id"]] = normalized
        return output
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


class CandidateRegistry:
    """Append-only in-run candidate provenance and verdict registry."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            raise FileExistsError(f"refusing existing candidate registry: {self.path}")
        self.path.write_text("", encoding="utf-8")
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        self._results: dict[str, dict[str, Any]] = {}
        self._sequence = 0

    @classmethod
    def resume(
        cls, path: Path, result_by_qid: Mapping[str, Mapping[str, Any]],
    ) -> "CandidateRegistry":
        """Resume an existing append-only registry without rewriting its prefix."""
        self = cls.__new__(cls)
        self.path = path
        self._lock = threading.Lock()
        self._entries = []
        self._results = {}
        self._sequence = 0
        latest: dict[str, dict[str, Any]] = {}
        registration_order: list[str] = []
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                event = json.loads(line)
                candidate_id = event.get("candidate_id")
                if not isinstance(candidate_id, str):
                    raise ValueError(f"registry line {number} has no candidate_id")
                self._sequence = max(self._sequence, int(event.get("registry_sequence", 0)))
                if event.get("registry_event") == "candidate_registered" and candidate_id not in latest:
                    registration_order.append(candidate_id)
                latest[candidate_id] = {
                    key: value for key, value in event.items()
                    if key not in {"registry_event", "registry_sequence"}
                }
        self._entries = [latest[candidate_id] for candidate_id in registration_order]
        for entry in self._entries:
            qid = entry["qid"]
            if qid not in result_by_qid:
                raise ValueError(f"registry qid absent from frozen results: {qid}")
            metadata = entry.get("metadata", {})
            candidate = {
                "answer": entry["answer"],
                "method": metadata.get("method", "resumed registry candidate"),
                "evidence": metadata.get("evidence", "resumed append-only registry"),
            }
            self._results[entry["candidate_id"]] = candidate_result(result_by_qid[qid], candidate)
        return self

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self._entries

    def register(
        self, *, qid: str, answer: str, head: str, stage: str,
        result: Mapping[str, Any], metadata: Mapping[str, Any], contract_variant: str,
    ) -> dict[str, Any]:
        normalized = _normalized(answer)
        digest = hashlib.sha256(
            json.dumps([qid, normalized, head, stage, contract_variant], separators=(",", ":")).encode()
        ).hexdigest()[:20]
        entry = {
            "qid": qid, "candidate_id": digest, "answer": answer,
            "normalized_answer": normalized, "head": head, "stage": stage,
            "scientific_hypothesis": metadata.get("scientific_hypothesis", "unresolved"),
            "contract_variant": contract_variant, "submitted": False, "verdict": None,
            "metadata": dict(metadata), "registration_sequence": None,
        }
        with self._lock:
            # Keep one row per provenance source even when several independent
            # heads converge on the same answer.  The submission controller
            # de-duplicates normalized values; the registry must not erase the
            # agreement signal used by the disagreement/ranking layers.
            for old in self._entries:
                if old["candidate_id"] == digest:
                    return old
            inherited = next((
                old for old in self._entries
                if old["qid"] == qid
                and old["normalized_answer"] == normalized
                and old["submitted"]
            ), None)
            if inherited is not None:
                entry["submitted"] = True
                entry["verdict"] = inherited["verdict"]
            entry["registration_sequence"] = len(self._entries) + 1
            self._entries.append(entry)
            self._results[digest] = copy.deepcopy(dict(result))
            self._append(entry, "candidate_registered")
        return entry

    def mark(self, candidate_id: str, verdict: str) -> None:
        with self._lock:
            entry = next(item for item in self._entries if item["candidate_id"] == candidate_id)
            entry["submitted"] = True
            entry["verdict"] = verdict
            self._append(entry, "verdict_update")

    def mark_equivalent(self, qid: str, normalized_answer: str, verdict: str) -> None:
        """Propagate one submitted verdict to all identical provenance rows."""
        with self._lock:
            for entry in self._entries:
                if entry["qid"] == qid and entry["normalized_answer"] == normalized_answer:
                    entry["submitted"] = True
                    entry["verdict"] = verdict
                    self._append(entry, "verdict_update")

    def submitted_values(self, qid: str) -> set[str]:
        return {
            item["normalized_answer"]
            for item in self._entries
            if item["qid"] == qid and item["submitted"]
        }

    def result(self, candidate_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._results[candidate_id])

    def pending(self, qid: str) -> list[dict[str, Any]]:
        return [item for item in self._entries if item["qid"] == qid and not item["submitted"]]

    def _append(self, entry: Mapping[str, Any], event: str) -> None:
        self._sequence += 1
        value = dict(entry)
        value["registry_event"] = event
        value["registry_sequence"] = self._sequence
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def analyze_disagreement(entries: Sequence[Mapping[str, Any]], qids: Sequence[str]) -> dict[str, Any]:
    questions = {}
    for qid in qids:
        candidates = [item for item in entries if item["qid"] == qid]
        if not candidates:
            questions[qid] = {"case": "no_candidates", "recommended_action": "targeted_rescue"}
            continue
        grounding = [
            tuple(str(item["metadata"].get(field, "unresolved")).strip().casefold() for field in (
                "data_object", "sample_scope", "observation_unit", "denominator", "comparison",
                "package", "version", "unit",
            ))
            for item in candidates
        ]
        answers = [item["normalized_answer"] for item in candidates]
        methods = [str(item["metadata"].get("method", "unresolved")).casefold() for item in candidates]
        hypotheses = [
            str(item["metadata"].get("scientific_hypothesis", "unresolved")).casefold()
            for item in candidates
        ]
        evidence_signatures = [
            str(item["metadata"].get("evidence", "unresolved")).casefold()
            for item in candidates
        ]
        ground_same = len(set(grounding)) == 1
        answer_same = len(set(answers)) == 1
        shared_upstream = (
            len(candidates) >= 3
            and answer_same
            and (len(set(hypotheses)) == 1 or len(set(evidence_signatures)) == 1)
        )
        if shared_upstream:
            case = "case_4_shared_upstream_assumption"
            action = "do_not_treat_as_independent_majority"
        elif ground_same and answer_same and len(set(methods)) >= 2:
            case = "case_1_stable_result_independent_methods"
            action = "rank_independent_agreement_high"
        elif ground_same and not answer_same:
            case = "case_2_implementation_disagreement"
            action = "inspect_implementation_package_rounding_filter_boundary"
        elif not ground_same:
            case = "case_3_grounding_disagreement"
            action = "semantic_grounding_arbitration"
        else:
            case = "insufficient_independent_evidence"
            action = "targeted_rescue"
        answer_counts = Counter(answers)
        questions[qid] = {
            "case": case,
            "recommended_action": action,
            "candidate_count": len(candidates),
            "grounding_signature_count": len(set(grounding)),
            "normalized_answer_counts": dict(answer_counts),
            "method_count": len(set(methods)),
            "scientific_hypothesis_count": len(set(hypotheses)),
            "shared_upstream_assumption": shared_upstream,
        }
    return {"questions": questions, "policy": "metadata-aware disagreement analysis v7"}


HEAD_TIE_ORDER = (
    "protocol_fidelity", "data_forensics", "method_triangulation", "full_recompute",
    "simple_baseline", "alternative_grounding", "adversarial_recompute", "scientific_specialist",
    "answer_contract", "b_core", "bounded_search",
)


def rank_candidates(
    candidates: Sequence[Mapping[str, Any]], *, protocol_named: bool,
    sibling_anchor: bool, disagreement: Mapping[str, Any] | None,
    evidence_aware: bool = True,
) -> list[dict[str, Any]]:
    order = {name: index for index, name in enumerate(HEAD_TIE_ORDER)}
    if not evidence_aware:
        return sorted((dict(item) for item in candidates), key=lambda item: (
            order.get(item["head"], 999), item.get("registration_sequence", 0),
        ))
    # Agreement means independent heads, not multiple formatting/provenance
    # rows emitted by one head.
    agreeing_heads: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        agreeing_heads[candidate["normalized_answer"]].add(candidate["head"])
    ranked = []
    for value in candidates:
        item = dict(value)
        metadata = item.get("metadata", {})
        score = 0
        reasons = []
        if protocol_named and item["head"] == "protocol_fidelity":
            score += 40; reasons.append("literal_protocol_fidelity")
        if sibling_anchor and metadata.get("validated_by_sibling_yes"):
            score += 25; reasons.append("sibling_yes_consistency")
        if len(agreeing_heads[item["normalized_answer"]]) >= 2:
            score += 15; reasons.append("cross_head_answer_agreement")
        if item["head"] == "data_forensics":
            score += 12; reasons.append("validated_data_integrity")
        if item["head"] == "method_triangulation":
            score += 14; reasons.append("independent_implementation")
        if item["contract_variant"].startswith("explicit_"):
            score += 16; reasons.append("explicit_answer_contract")
        elif item["contract_variant"] != "raw":
            score += 4; reasons.append("deterministic_contract_variant")
        evidence = str(metadata.get("evidence", ""))
        if "analysis/scripts/" in evidence:
            score += 8; reasons.append("reproducible_script_evidence")
        confidence = str(metadata.get("confidence", "low")).casefold()
        score += {"high": 8, "medium": 4}.get(confidence, 0)
        unresolved_fields = sum(
            str(metadata.get(field, "unresolved")).strip().casefold() in {"", "unknown", "unresolved"}
            for field in (
                "data_object", "sample_scope", "observation_unit", "denominator",
                "comparison", "method", "unit",
            )
        )
        if unresolved_fields:
            score -= min(14, unresolved_fields * 2)
            reasons.append("unsupported_assumption_penalty")
        if metadata.get("metadata_status") == "controller_fallback":
            score -= 8; reasons.append("metadata_fallback_penalty")
        if disagreement and disagreement.get("case") == "case_3_grounding_disagreement" and item["head"] == "alternative_grounding":
            score += 20; reasons.append("grounding_arbitration_match")
        item["ranking_score"] = score
        item["ranking_reasons"] = reasons
        ranked.append(item)
    return sorted(ranked, key=lambda item: (
        -item["ranking_score"], order.get(item["head"], 999),
        item.get("registration_sequence", 0),
    ))


def grounding_by_qid(workspace: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads((workspace / "analysis/grounding.json").read_text(encoding="utf-8"))
        return {item["question_id"]: item for item in value.get("questions", [])}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
