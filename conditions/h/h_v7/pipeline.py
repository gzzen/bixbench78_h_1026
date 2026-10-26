# Path: conditions/h/h_v7/pipeline.py
# Purpose: h_v7 (D-v7 localized, metadata-aware, cumulative Yes/No repair) as an H variant (port of ngch runner_v7.py).
"""D-v7 over a frozen parent.

Differences from ngch ``runner_v7.py``, all forced by the frozen-parent design:

* the parent (B, or B plus a C level) replaces the fresh B core, so the parent is
  not re-run and its Yes/No comes from the parent scoring (Yes = locked);
* every ``judge`` call is a submission to the feedback oracle (the sibling grader);
  a question's current answer is Yes exactly when it is locked or was accepted in
  this run, which is what ngch's repeated ``feedback_for(current)`` computed;
* localization, disagreement and bounded-search files go to
  ``<run_root>/capsules/<slug>/analysis/``, never into the frozen B workspace, and each
  capsule keeps its own append-only ``candidate_registry.jsonl`` (lookups are per
  question, so this equals ngch's single registry and lets a capsule restart cleanly);
* the parent's candidates keep ngch's registry head label ``b_core`` (the ranking
  tie order names it); its stage is reported as ``parent``.
"""
from __future__ import annotations

import argparse
import copy
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from common.benchmark.a84_contract import write_json
from conditions.h.core.feedback import FeedbackOracle
from conditions.h.core.rescue_main import HVariant, RunContext
from conditions.h.core.stage import prompt_text, run_stage
from conditions.h.h_v7.contracts import contract_variants, explicit_integer_domain, protocol_is_named, specs_for_rows
from conditions.h.h_v7.controller import (
    CandidateRegistry,
    analyze_disagreement,
    candidate_result,
    fallback_metadata,
    grounding_by_qid,
    load_candidate_metadata,
    rank_candidates,
)
from conditions.h.h_v7.localizer import localize_capsule

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
INITIAL_HEADS = (
    "full_recompute",
    "simple_baseline",
    "data_forensics",
    "method_triangulation",
    "alternative_grounding",
)
PROMPTS = {
    name: PROMPT_DIR / f"v7_{name}.md"
    for name in (
        *INITIAL_HEADS,
        "protocol_fidelity",
        "adversarial_recompute",
        "scientific_specialist",
    )
}
STAGE_ORDER = (
    "parent",
    "early_answer_contract",
    "early_protocol_fidelity",
    "localized_multihead",
    "deferred_protocol_fidelity",
    "adversarial_recompute",
    "semantic_regrounding",
    "scientific_specialist",
    "bounded_search",
)
ABLATIONS = {
    "without_error_localizer",
    "without_sibling_anchors",
    "without_early_protocol_fidelity",
    "without_disagreement_analyzer",
    "without_evidence_aware_ranking",
}


def _result_map(value: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["question_id"]: copy.deepcopy(item) for item in value["results"]}


def _replace_result(value: Mapping[str, Any], qid: str, item: Mapping[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(dict(value))
    output["results"] = [
        copy.deepcopy(dict(item)) if old["question_id"] == qid else old
        for old in output["results"]
    ]
    return output


def _question_by_id(rows: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    return {row["question_id"]: str(row["question"]) for row in rows}


def _feedback(value: Mapping[str, Any], accepted_stage: Mapping[str, str]) -> list[dict[str, str]]:
    """Yes exactly for questions whose current answer is locked or was accepted by a Yes submission."""
    return [{"question_id": item["question_id"], "feedback": "yes" if item["question_id"] in accepted_stage else "no"}
            for item in value["results"]]


def _specialist_route(question: str) -> str:
    q = question.casefold()
    patterns = (
        ("phylogenetics", r"phylo|tree|branch|alignment|newick|clipkit|phykit"),
        ("enrichment", r"enrich|pathway|gene ontology|\bgo\b|kegg|reactome"),
        ("single_cell", r"single[- ]cell|seurat|scanpy|cell type|cluster marker"),
        ("differential_expression", r"deseq|edger|limma|differential|log2fold|padj"),
        ("genomic_intervals", r"bedtools|interval|overlap|chromosome|genomic coordinate"),
        ("sequence_statistics", r"fasta|sequence|nucleotide|amino acid|motif|gc content"),
        ("imaging", r"image|pixel|segmentation|area|microscopy|roi"),
    )
    for name, pattern in patterns:
        if re.search(pattern, q):
            return name
    return "general_quantitative_analysis"


def _register_result(
    *, registry: CandidateRegistry, proposed: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
    active_qids: set[str], head: str, stage: str, workspace: Path | None,
    grounding: Mapping[str, Mapping[str, Any]], contracts: Mapping[str, Any],
    localization: Mapping[str, Any], include_contract_variants: bool = True,
) -> list[dict[str, Any]]:
    questions = _question_by_id(rows)
    supplied = load_candidate_metadata(workspace)
    registered = []
    for item in proposed["results"]:
        qid = item["question_id"]
        if qid not in active_qids:
            continue
        diagnostic = localization.get("question_diagnostics", {}).get(qid, {})
        hypothesis = ", ".join(diagnostic.get("likely_failure_types", [])) or "unresolved"
        for candidate in item.get("candidates", []):
            raw_answer = str(candidate.get("answer", "")).strip()
            if not raw_answer:
                continue
            variants = (
                contract_variants(questions[qid], raw_answer, contracts[qid])
                if include_contract_variants else [("raw", raw_answer)]
            )
            for variant, answer in variants:
                rewritten = copy.deepcopy(candidate)
                rewritten["answer"] = answer
                result_item = candidate_result(item, rewritten)
                metadata = copy.deepcopy(supplied.get(qid))
                if metadata is None:
                    metadata = fallback_metadata(
                        qid=qid, answer=answer, head=head, result=item,
                        grounding=grounding.get(qid, {}), contract=contracts[qid],
                        hypothesis=hypothesis,
                    )
                metadata.update({
                    "question_id": qid,
                    "answer": answer,
                    "source_head": head,
                    "validated_by_sibling_yes": bool(
                        diagnostic.get("validated_by_sibling_yes")
                    ),
                })
                registered.append(registry.register(
                    qid=qid, answer=answer, head=head, stage=stage,
                    result=result_item, metadata=metadata, contract_variant=variant,
                ))
    return registered


def _submit_pending(
    *, current: dict[str, Any], qids: Sequence[str], registry: CandidateRegistry,
    oracle: FeedbackOracle, label: str, protocols: Mapping[str, Any],
    localization: Mapping[str, Any], disagreement: Mapping[str, Any],
    evidence_aware: bool, attempts: list[dict[str, Any]],
    accepted_stage: dict[str, str], stage: str,
) -> dict[str, Any]:
    output = copy.deepcopy(current)
    for qid in qids:
        if qid in accepted_stage:
            continue
        pending = registry.pending(qid)
        diagnostic = localization.get("question_diagnostics", {}).get(qid, {})
        ranked = rank_candidates(
            pending,
            protocol_named=protocol_is_named(protocols[qid]),
            sibling_anchor=bool(diagnostic.get("validated_by_sibling_yes")),
            disagreement=disagreement.get("questions", {}).get(qid),
            evidence_aware=evidence_aware,
        )
        tried = registry.submitted_values(qid)
        for candidate in ranked:
            normalized = candidate["normalized_answer"]
            if normalized in tried:
                continue
            support_heads = sorted({
                item["head"] for item in registry.entries
                if item["qid"] == qid and item["normalized_answer"] == normalized
            })
            correct = oracle.submit(qid, candidate["answer"], f"{label}/{stage}")
            verdict = "yes" if correct else "no"
            registry.mark_equivalent(qid, normalized, verdict)
            attempts.append({
                "question_id": qid,
                "candidate_id": candidate["candidate_id"],
                "stage": stage,
                "source_head": candidate["head"],
                "supporting_heads": support_heads,
                "answer": candidate["answer"],
                "contract_variant": candidate.get("contract_variant", "raw"),
                "ranking_score": candidate.get("ranking_score"),
                "ranking_reasons": candidate.get("ranking_reasons", []),
                "verdict": verdict,
            })
            tried.add(normalized)
            if correct:
                output = _replace_result(output, qid, registry.result(candidate["candidate_id"]))
                accepted_stage.setdefault(qid, stage)
                break
    return output


def _bounded_search(
    *, current: dict[str, Any], rows: Sequence[Mapping[str, Any]], registry: CandidateRegistry,
    max_domain: int, grounding: Mapping[str, Mapping[str, Any]],
    contracts: Mapping[str, Any], localization: Mapping[str, Any], active_qids: set[str],
) -> dict[str, Any]:
    current_items = _result_map(current)
    report: dict[str, Any] = {"questions": {}, "policy": "explicit finite public integer domains only"}
    for row in rows:
        qid = row["question_id"]
        if qid not in active_qids:
            continue
        domain = explicit_integer_domain(str(row["question"]), max_domain)
        if domain is None or domain[1] - domain[0] + 1 > max_domain:
            continue
        prior_values = []
        for entry in registry.entries:
            if entry["qid"] == qid:
                try:
                    prior_values.append(int(entry["answer"]))
                except ValueError:
                    pass
        midpoint = (domain[0] + domain[1]) // 2
        center = prior_values[-1] if prior_values else midpoint
        values = sorted(range(domain[0], domain[1] + 1), key=lambda value: (abs(value - center), value))
        report["questions"][qid] = {
            "search_domain": [domain[0], domain[1]],
            "scientific_center": center,
            "candidate_order": values,
            "number_of_candidates": len(values),
        }
        template = current_items[qid]
        for value in values:
            answer = str(value)
            candidate = {
                "answer": answer,
                "method": "explicit public-domain bounded Yes/No search",
                "evidence": "public question proves finite integer domain",
            }
            metadata = fallback_metadata(
                qid=qid, answer=answer, head="bounded_search", result=template,
                grounding=grounding.get(qid, {}), contract=contracts[qid],
                hypothesis="finite public integer domain",
            )
            registry.register(
                qid=qid, answer=answer, head="bounded_search", stage="bounded_search",
                result=candidate_result(template, candidate), metadata=metadata,
                contract_variant="explicit_integer_domain",
            )
    return report


def run_capsule(
    *, item: Mapping[str, Any], rows: list[dict[str, Any]], core: dict[str, Any],
    initial_feedback: list[dict[str, str]], core_workspace: Path, out_dir: Path, root: Path,
    timeout: int, oracle: FeedbackOracle, ablations: set[str], enable_bounded: bool,
    max_bounded_domain: int,
) -> dict[str, Any]:
    """ngch ``_run_one_capsule`` over a frozen parent capsule."""
    qids = [row["question_id"] for row in rows]
    label = f"v7/{item['short_id']}"
    protocol_payload, contract_payload = specs_for_rows(rows)
    protocols, contracts = protocol_payload["questions"], contract_payload["questions"]
    registry = CandidateRegistry(out_dir / "candidate_registry.jsonl")
    attempts: list[dict[str, Any]] = []
    accepted_stage: dict[str, str] = {}
    head_runs: list[dict[str, Any]] = []
    calls = 0
    failures = []
    usage: dict[str, float] = {}

    current = copy.deepcopy(core)
    grounding = grounding_by_qid(core_workspace)
    for result_item, feedback_item in zip(current["results"], initial_feedback):
        qid = result_item["question_id"]
        if feedback_item["feedback"] == "yes":
            accepted_stage[qid] = "parent"
        candidates = result_item.get("candidates", [])
        if not candidates:
            continue
        entries = []
        for candidate in candidates:
            answer = str(candidate["answer"]).strip()
            metadata = fallback_metadata(
                qid=qid, answer=answer, head="b_core", result=result_item,
                grounding=grounding.get(qid, {}), contract=contracts[qid],
                hypothesis="parent core hypothesis",
            )
            entries.append(registry.register(
                qid=qid, answer=answer, head="b_core", stage="parent",
                result=candidate_result(result_item, candidate), metadata=metadata,
                contract_variant="raw",
            ))
        entry = entries[0]
        registry.mark_equivalent(qid, entry["normalized_answer"], feedback_item["feedback"])
        attempts.append({
            "question_id": qid, "candidate_id": entry["candidate_id"], "stage": "parent",
            "source_head": "b_core", "supporting_heads": ["b_core"], "answer": str(candidates[0]["answer"]).strip(),
            "ranking_score": None, "ranking_reasons": [], "verdict": feedback_item["feedback"],
        })

    sibling_anchors = "without_sibling_anchors" not in ablations
    localization = localize_capsule(
        rows=rows, feedback=initial_feedback, workspace=core_workspace,
        protocol_spec=protocol_payload, answer_contract=contract_payload,
        sibling_anchors=sibling_anchors,
    )
    if "without_error_localizer" in ablations:
        localization["ablation"] = "without_error_localizer"
        localization["recommended_heads"] = list(INITIAL_HEADS)
        localization["suspected_failure_layers"] = ["unlocalized"]
    write_json(out_dir / "analysis/protocol_spec.json", protocol_payload)
    write_json(out_dir / "analysis/answer_contract.json", contract_payload)
    write_json(out_dir / "analysis/error_localization.json", localization)
    evidence_aware = "without_evidence_aware_ranking" not in ablations

    def submit(stage: str, disagreement: Mapping[str, Any], targets: Sequence[str] = qids) -> None:
        nonlocal current
        current = _submit_pending(
            current=current, qids=targets, registry=registry, oracle=oracle, label=label,
            protocols=protocols, localization=localization, disagreement=disagreement,
            evidence_aware=evidence_aware, attempts=attempts, accepted_stage=accepted_stage, stage=stage,
        )

    unresolved = {qid for qid in qids if qid not in accepted_stage}
    _register_result(
        registry=registry, proposed=current, rows=rows, active_qids=unresolved,
        head="answer_contract", stage="early_answer_contract", workspace=None,
        grounding=grounding, contracts=contracts, localization=localization,
        include_contract_variants=True,
    )
    submit("early_answer_contract", {"questions": {}})

    def run_and_register(name: str, stage: str, prior: dict[str, Any], disagreement=None):
        nonlocal calls
        feedback = _feedback(prior, accepted_stage)
        payloads: dict[str, Any] = {
            "error_localization.json": localization,
            "protocol_spec.json": {"questions": protocols},
            "answer_contract.json": {"questions": contracts},
        }
        if disagreement is not None:
            payloads["head_disagreement.json"] = disagreement
        if name == "scientific_specialist":
            questions = _question_by_id(rows)
            payloads["specialist_routing.json"] = {qid: _specialist_route(questions[qid]) for qid in questions}
        proposed, record = run_stage(
            head=name, stage=stage, prior=prior, feedback=feedback, rows=rows, root=root,
            timeout=timeout, prompt_path=PROMPTS[name], extra_payloads=payloads,
        )
        calls += record.get("total_agent_calls", 0)
        for key, value in record.get("usage", {}).items():
            usage[key] = usage.get(key, 0) + value
        if not record.get("accepted"):
            failures.append({"head": name, "stage": stage, "reason": record.get("failure_reason")})
        active = {entry["question_id"] for entry in feedback if entry["feedback"] == "no"}
        workspace = Path(record["workspace"]) if record.get("workspace") else None
        _register_result(
            registry=registry, proposed=proposed, rows=rows, active_qids=active,
            head=name, stage=stage, workspace=workspace, grounding=grounding,
            contracts=contracts, localization=localization, include_contract_variants=True,
        )
        head_runs.append({"head": name, "stage": stage, "final": proposed, "record": record})
        return proposed

    named_unresolved = [
        qid for qid in qids
        if qid not in accepted_stage and protocol_is_named(protocols[qid])
    ]
    protocol_ran = False
    if named_unresolved and "without_early_protocol_fidelity" not in ablations:
        run_and_register("protocol_fidelity", "early_protocol_fidelity", current)
        protocol_ran = True
        submit("early_protocol_fidelity", {"questions": {}})

    multihead_anchor = copy.deepcopy(current)
    recommended = [
        head for head in INITIAL_HEADS if head in localization.get("recommended_heads", [])
    ]
    if not recommended:
        recommended = ["method_triangulation", "simple_baseline"]
    initial_start = len(registry.entries)
    for name in recommended:
        run_and_register(name, "localized_multihead", multihead_anchor)
    initial_entries = registry.entries[initial_start:]
    unresolved_now = [qid for qid in qids if qid not in accepted_stage]
    if "without_disagreement_analyzer" in ablations:
        disagreement = {
            "questions": {qid: {"case": "disabled_by_ablation"} for qid in unresolved_now},
            "policy": "disabled_by_ablation",
        }
    else:
        disagreement = analyze_disagreement(initial_entries, unresolved_now)
    write_json(out_dir / "analysis/head_disagreement.json", disagreement)
    submit("localized_multihead", disagreement)

    if not protocol_ran and any(
        protocol_is_named(protocols[qid]) and qid not in accepted_stage for qid in qids
    ):
        run_and_register("protocol_fidelity", "deferred_protocol_fidelity", current, disagreement)
        submit("deferred_protocol_fidelity", disagreement)

    if len(accepted_stage) < len(qids):
        run_and_register("adversarial_recompute", "adversarial_recompute", current, disagreement)
        submit("adversarial_recompute", disagreement)

    cases = disagreement.get("questions", {})
    needs_semantic = any(
        qid not in accepted_stage and cases.get(qid, {}).get("case") == "case_3_grounding_disagreement"
        for qid in qids
    ) or "alternative_grounding" not in recommended
    if len(accepted_stage) < len(qids) and needs_semantic:
        run_and_register("alternative_grounding", "semantic_regrounding", current, disagreement)
        submit("semantic_regrounding", disagreement)

    if len(accepted_stage) < len(qids):
        run_and_register("scientific_specialist", "scientific_specialist", current, disagreement)
        submit("scientific_specialist", disagreement)

    bounded_report: dict[str, Any] = {"questions": {}, "policy": "disabled"}
    if enable_bounded and len(accepted_stage) < len(qids):
        bounded_report = _bounded_search(
            current=current, rows=rows, registry=registry, max_domain=max_bounded_domain,
            grounding=grounding, contracts=contracts, localization=localization,
            active_qids={qid for qid in qids if qid not in accepted_stage},
        )
        submit("bounded_search", disagreement, list(bounded_report["questions"]))
        for qid, value in bounded_report["questions"].items():
            value["number_of_submissions"] = sum(
                attempt["question_id"] == qid and attempt["stage"] == "bounded_search"
                for attempt in attempts
            )
        write_json(out_dir / "analysis/bounded_search.json", bounded_report)

    return {
        "short_id": item["short_id"], "capsule_uuid": item["capsule_uuid"],
        "parent_result": core, "final_result": current,
        "initial_feedback": initial_feedback, "final_feedback": _feedback(current, accepted_stage),
        "error_localization": localization, "head_disagreement": disagreement,
        "head_runs": head_runs, "attempts": attempts, "accepted_stage": accepted_stage,
        "bounded_search": bounded_report, "model_calls": calls, "failures": failures,
        "total_agent_calls": calls, "usage": usage,
    }


def stage_metrics(episodes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    accepted_at: dict[str, str] = {}
    attempts = []
    for episode in episodes:
        accepted_at.update(episode["accepted_stage"])
        attempts.extend(episode["attempts"])
    calls_by_stage: dict[str, int] = defaultdict(int)
    for episode in episodes:
        for run in episode["head_runs"]:
            calls_by_stage[run["stage"]] += run["record"].get("total_agent_calls", 0)
    cumulative = 0
    output = []
    for stage in STAGE_ORDER:
        new = sum(value == stage for value in accepted_at.values())
        cumulative += new
        stage_attempts = [item for item in attempts if item["stage"] == stage]
        solved = [item for item in stage_attempts if item["verdict"] == "yes"]
        output.append({
            "stage": stage,
            "newly_accepted": new,
            "newly_solved_qids": [item["question_id"] for item in solved],
            "source_strategies": {item["question_id"]: item["source_head"] for item in solved},
            "cumulative_accepted": cumulative,
            "submissions": len(stage_attempts),
            "model_calls": calls_by_stage.get(stage, 0),
            "yes": sum(item["verdict"] == "yes" for item in stage_attempts),
            "no": sum(item["verdict"] == "no" for item in stage_attempts),
        })
    return output


class LocalizedV7(HVariant):
    """One task per triggered capsule; its heads run sequentially so each rescue sees the latest locked state."""

    name = "h_v7"
    revision = "H-v7 localized metadata-aware cumulative Yes/No repair (port of ngch D-v7)"
    package_dir = Path(__file__).resolve().parent
    # protocol fidelity (early or deferred) + up to five localized heads + adversarial + semantic + specialist.
    max_calls_per_capsule = 1 + len(INITIAL_HEADS) + 3

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--ablation", action="append", choices=sorted(ABLATIONS), default=[])
        parser.add_argument("--enable-bounded", action="store_true",
                            help="also submit explicit public integer domains (reported separately)")
        parser.add_argument("--max-bounded-domain", type=int, default=200)

    def options(self, args: argparse.Namespace) -> dict[str, Any]:
        if args.max_bounded_domain < 1:
            raise ValueError("--max-bounded-domain must be positive")
        return {"ablations": sorted(set(args.ablation)), "enable_bounded": args.enable_bounded,
                "max_bounded_domain": args.max_bounded_domain, "initial_head_roles": list(INITIAL_HEADS),
                "general_rescue_order": ["protocol_fidelity", "adversarial_recompute", "semantic_regrounding",
                                         "scientific_specialist", "optional_bounded_search"]}

    def prompts(self) -> dict[str, str]:
        return {name: prompt_text(path) for name, path in PROMPTS.items()}

    def tasks(self, parent, capsules) -> list[dict[str, Any]]:
        return [{"task_id": capsule["short_id"], "capsule": capsule} for capsule in capsules]

    def task_dirs(self, task, run_root: Path) -> list[Path]:
        slug = f"{task['capsule']['short_id']}__{task['capsule']['capsule_uuid']}"
        return list((run_root / "heads").glob(f"*/*/{slug}")) + [run_root / "capsules" / slug]

    def run_task(self, task, ctx: RunContext) -> dict[str, Any]:
        capsule = task["capsule"]
        slug = f"{capsule['short_id']}__{capsule['capsule_uuid']}"
        episode = run_capsule(
            item=capsule, rows=ctx.parent.capsule_rows(capsule), core=ctx.parent.capsule_payload(capsule),
            initial_feedback=ctx.parent.capsule_feedback(capsule),
            core_workspace=ctx.parent.workspaces[capsule["capsule_uuid"]],
            out_dir=ctx.run_root / "capsules" / slug, root=ctx.run_root, timeout=ctx.timeout,
            oracle=ctx.oracle, ablations=set(ctx.options["ablations"]),
            enable_bounded=ctx.options["enable_bounded"], max_bounded_domain=ctx.options["max_bounded_domain"],
        )
        return {"task_id": task["task_id"], **episode}

    def select(self, records, ctx: RunContext):
        final = copy.deepcopy(ctx.parent.core)
        replaced = {}
        for episode in records:
            for item in episode["final_result"]["results"]:
                final[item["question_id"]] = copy.deepcopy(item)
            for qid, stage in episode["accepted_stage"].items():
                if stage != "parent":
                    replaced[qid] = {"source": stage, "rule": "yes_no_submission"}
        bounded = sum(item["source"] == "bounded_search" for item in replaced.values())
        return final, replaced, {
            "stage_metrics": stage_metrics(records),
            "accepted_without_bounded": len(replaced) - bounded,
            "accepted_with_bounded": len(replaced),
            "total_submissions": sum(len(episode["attempts"]) for episode in records),
            "bounded_submissions": sum(item["stage"] == "bounded_search"
                                       for episode in records for item in episode["attempts"]),
            "stage_failures": [failure for episode in records for failure in episode["failures"]],
        }
