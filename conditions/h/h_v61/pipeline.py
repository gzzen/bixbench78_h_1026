# Path: conditions/h/h_v61/pipeline.py
# Purpose: h_v61 (D-v6.1 balanced five-family policy) as an H variant: tasks, execution and answer selection.
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from conditions.h.core.aggregation import first_answer, reference_free_select, sequential_yes_no_select
from conditions.h.core.answer_contract import normalize_result
from conditions.h.core.rescue_main import HVariant, RunContext
from conditions.h.core.stage import prompt_text
from conditions.h.h_v61 import heads
from conditions.h.h_v61.policy import COVERAGE_HEADS, EXECUTION_HEADS, SELECTOR_HEADS

CONTRACT_PREFIXES = ((1, "contract_1dp__"), (0, "contract_0dp__"))


def family_order(families) -> tuple[str, ...]:
    return tuple(name for family in families
                 for name in (family, "contract_1dp__" + family, "contract_0dp__" + family))


class BalancedV61(HVariant):
    """Five head families per triggered capsule; final pick by consensus over the selector heads."""

    name = "h_v61"
    revision = "H-v6.1 balanced five-family multihead (port of ngch D-v6.1)"
    package_dir = Path(__file__).resolve().parent
    # full: B (+1 policy retry) + conservative; baseline_polling: A + poll; full_aggressive: B (+1) + 2 repairs;
    # data_forensics and method_triangulation: one repair each.
    max_calls_per_capsule = 3 + 2 + 4 + 1 + 1

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--selection-mode", choices=("reference_free", "sequential_yes_no"),
                            default="reference_free",
                            help="reference_free: D-v6.1 selector heads; sequential_yes_no: D-v6.2 Yes/No submissions")

    def options(self, args: argparse.Namespace) -> dict[str, Any]:
        return {"selection_mode": args.selection_mode, "execution_heads": list(EXECUTION_HEADS),
                "selector_heads": list(SELECTOR_HEADS), "coverage_heads": list(COVERAGE_HEADS),
                "answer_contract_profiles": ["raw", "1dp", "0dp"]}

    def prompts(self) -> dict[str, str]:
        paths = {"conservative": heads.CONSERVATIVE, "aggressive": heads.AGGRESSIVE, **heads.REFLECTION_PROMPTS}
        return {name: prompt_text(path) for name, path in paths.items()}

    def tasks(self, parent, capsules) -> list[dict[str, Any]]:
        return [{"task_id": f"{head}__{capsule['short_id']}", "head": head, "capsule": capsule}
                for head in EXECUTION_HEADS for capsule in capsules]

    def task_dirs(self, task, run_root: Path) -> list[Path]:
        slug = f"{task['capsule']['short_id']}__{task['capsule']['capsule_uuid']}"
        base = run_root / "heads" / task["head"]
        return list(base.glob(f"*/{slug}")) + list(base.glob(f"initial/*/{slug}"))

    def run_task(self, task, ctx: RunContext) -> dict[str, Any]:
        capsule, head = task["capsule"], task["head"]
        rows = ctx.parent.capsule_rows(capsule)
        if head in heads.LEGACY_HEADS:
            run = heads.run_legacy_head(head, capsule, rows, ctx.run_root, ctx.timeout, ctx.oracle)
        else:
            run = heads.run_reflection_head(head, ctx.parent.capsule_payload(capsule), ctx.parent.capsule_feedback(capsule),
                                            rows, ctx.run_root, ctx.timeout)
        usage: dict[str, float] = {}
        for record in run["stages"]:
            for key, value in record.get("usage", {}).items():
                usage[key] = usage.get(key, 0) + value
        return {"task_id": task["task_id"], "head": head, "short_id": capsule["short_id"],
                "capsule_uuid": capsule["capsule_uuid"], **run,
                "total_agent_calls": sum(record.get("total_agent_calls", 0) for record in run["stages"]),
                "usage": usage}

    def select(self, records, ctx: RunContext):
        questions = {qid: row["question"] for qid, row in ctx.parent.rows.items()}
        head_results: dict[str, dict[str, dict]] = {}
        for record in records:
            variants = {record["head"]: record["final"]}
            for digits, prefix in CONTRACT_PREFIXES:
                variants[prefix + record["head"]] = normalize_result(record["final"], questions, digits)
            for name, value in variants.items():
                head_results.setdefault(name, {}).update({item["question_id"]: item for item in value["results"]})
        run_qids = {qid for record in records for qid in (item["question_id"] for item in record["final"]["results"])}
        targets = set(ctx.parent.targets) & run_qids
        core = ctx.parent.core
        mode = ctx.options["selection_mode"]
        all_order = family_order(EXECUTION_HEADS)
        if mode == "reference_free":
            final, decisions = reference_free_select(core, head_results, targets, SELECTOR_HEADS)
            replaced = {d["question_id"]: {"source": d["source"], "rule": d["rule"]}
                        for d in decisions if d["source"] != "parent"}
            order = list(SELECTOR_HEADS)
        else:
            order = list(dict.fromkeys((*SELECTOR_HEADS, *all_order)))
            pairs = [(name, qid, first_answer(head_results.get(name, {}).get(qid, {})))
                     for name in order for qid in sorted(targets)]
            verdicts = ctx.oracle.judge_many([(qid, answer) for _, qid, answer in pairs if answer], "selection")
            head_verdicts: dict[str, dict[str, bool]] = {}
            for name, qid, answer in pairs:
                head_verdicts.setdefault(name, {})[qid] = bool(answer) and verdicts[(qid, answer)]
            locked = set(ctx.parent.locked)
            final, decisions = sequential_yes_no_select(core, head_results, {qid: qid in locked for qid in core},
                                                        head_verdicts, order)
            replaced = {d["question_id"]: {"source": d["accepted_source"], "rule": "sequential_yes_no"}
                        for d in decisions if d["accepted_source"] not in (None, "parent")}
        return final, replaced, {"mode": mode, "head_order": order, "all_heads": list(all_order),
                                 "decisions": decisions}
