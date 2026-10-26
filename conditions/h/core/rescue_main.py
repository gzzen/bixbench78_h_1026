# Path: conditions/h/core/rescue_main.py
# Purpose: CLI, orchestration, protocol snapshot, resume, freeze and report shared by every H variant (h_v61, h_v7).
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.benchmark.a84_contract import write_json
from common.integrity.opencode_capture import agent_manifest
from common.integrity.progress import progress_log
from conditions.c.c_minimal import agent_call
from conditions.h.core import stage
from conditions.h.core.feedback import GRADER, JUDGES, FeedbackOracle
from conditions.h.core.parent import HParent, digest, load_h_parent

ROOT = Path(__file__).resolve().parents[3]
CORE_DIR = Path(__file__).resolve().parent
CONTROLLER_SOURCES = [ROOT / path for path in (
    "common/integrity/opencode_capture.py", "common/integrity/attempt_container.py",
    "conditions/c/c_minimal/agent_call.py", "scripts/export_predictions.py",
)]
# Workspace content that is never frozen: capsule data, temp files and OpenCode home/state.
UNFROZEN_PARTS = {"data", "tmp", "home", "__pycache__"}


@dataclass
class RunContext:
    run_root: Path
    timeout: int
    oracle: FeedbackOracle
    parent: HParent
    options: dict[str, Any]


class HVariant:
    """One H variant: its resumable tasks, how a task runs, and how the final answers are selected."""

    name = "h"
    revision = "H"
    package_dir = CORE_DIR
    max_calls_per_capsule = 0

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        pass

    def options(self, args: argparse.Namespace) -> dict[str, Any]:
        return {}

    def prompts(self) -> dict[str, str]:
        return {}

    def tasks(self, parent: HParent, capsules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def task_dirs(self, task: dict[str, Any], run_root: Path) -> list[Path]:
        raise NotImplementedError

    def run_task(self, task: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        raise NotImplementedError

    def select(self, records: list[dict[str, Any]], ctx: RunContext) -> tuple[dict[str, dict], dict[str, dict], dict]:
        """(final item per question, {qid: provenance} of replaced targets, selection details)."""
        raise NotImplementedError


def parent_tag(generation: Path) -> str:
    name = generation.name
    return re.sub(r"_generation\.json$", "", name) if name.endswith("_generation.json") else generation.stem


def protocol_sources(variant: HVariant) -> list[Path]:
    files: list[Path] = []
    for directory in dict.fromkeys((CORE_DIR, variant.package_dir)):
        files += [path for path in sorted(directory.rglob("*")) if path.is_file()
                  and path.suffix in {".py", ".json", ".md"} and not path.name.startswith("test_")
                  and "__pycache__" not in path.parts]
    return files + CONTROLLER_SOURCES


def task_record_path(run_root: Path, task: dict[str, Any]) -> Path:
    return run_root / "tasks" / f"{task['task_id']}.json"


def finished_task(run_root: Path, task: dict[str, Any]) -> dict[str, Any] | None:
    """A task an earlier, interrupted run finished; its record is written last, after every workspace."""
    try:
        return json.loads(task_record_path(run_root, task).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def freeze_run_root(run_root: Path) -> list[dict[str, str]]:
    artifacts = []
    for path in sorted(run_root.rglob("*")):
        relative = path.relative_to(run_root)
        if path.is_symlink() or not path.is_file() or UNFROZEN_PARTS & set(relative.parts[:-1]) or path.suffix == ".pyc":
            continue
        artifacts.append({"path": str(path.relative_to(ROOT)), "sha256": digest(path)})
    return artifacts


def main(variant: HVariant) -> None:
    parser = argparse.ArgumentParser(description=f"{variant.revision}: multihead rescue over a frozen B or C parent")
    parser.add_argument("--parent-generation", type=Path, required=True,
                        help="frozen B or C-level generation JSON (freeze.status=frozen_before_private_scoring)")
    parser.add_argument("--parent-scoring", type=Path, required=True,
                        help="post-freeze scoring of that generation's export (status=completed_posthoc_after_freeze)")
    parser.add_argument("--parent-registry", type=Path,
                        help="locked/target registry shared by all H variants; default analysis/<parent-tag>_h_parent.json")
    parser.add_argument("--model", help="default: the frozen B run's model")
    parser.add_argument("--reasoning-effort", help="default: the frozen B run's reasoning effort")
    parser.add_argument("--feedback-judge", choices=JUDGES, default="deepseek",
                        help="grader judge for in-run Yes/No (surrogate = offline rule, no DeepSeek calls)")
    parser.add_argument("--grader", type=Path, default=GRADER, help="sibling grader score.py")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="continue an interrupted run in --run-root: keep finished tasks, redo unfinished ones")
    parser.add_argument("--allow-no-locked", action="store_true",
                        help="execute even when the parent scoring locks no question (normally a broken export)")
    parser.add_argument("--limit", type=int, help="pilot: only the first N triggered capsules")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=3600, help="seconds per agent call")
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    variant.add_arguments(parser)
    args = parser.parse_args()

    generation_path = args.parent_generation.resolve()
    scoring_path = args.parent_scoring.resolve()
    tag = parent_tag(generation_path)
    registry_path = (args.parent_registry or ROOT / f"analysis/{tag}_h_parent.json").resolve()
    run_root = (args.run_root or ROOT / f"runs/{tag}_{variant.name}").resolve()
    output = (args.output or ROOT / f"analysis/{tag}_{variant.name}_generation.json").resolve()
    report = (args.report or ROOT / f"reports/{tag}_{variant.name.upper()}_GENERATION.md").resolve()

    parent = load_h_parent(generation_path, scoring_path, registry_path)
    registry = parent.registry
    model = args.model or registry["b_model"] or agent_call.MODEL
    effort = args.reasoning_effort or registry["b_reasoning_effort"] or agent_call.REASONING_EFFORT
    agent = agent_manifest(model, effort)  # validates model/effort before any call
    stage.configure(model, effort)
    if not 1 <= args.workers <= 19:
        raise SystemExit("workers must be between 1 and 19")
    if args.limit is not None and args.limit < 1:
        raise ValueError("pilot limit must be positive")
    if args.execute and not args.resume and run_root.exists() and any(run_root.iterdir()):
        raise SystemExit("refusing nonempty H run root (pass --resume to continue it)")
    if args.execute and output.exists():
        raise SystemExit("refusing to overwrite an existing H generation output")
    if args.execute and not registry["locked_question_ids"] and not args.allow_no_locked:
        raise SystemExit("parent scoring locks no question; check the parent export and scoring, "
                         "or pass --allow-no-locked")
    capsules = parent.triggered()
    if args.limit is not None:
        capsules = capsules[:args.limit]
    tasks = variant.tasks(parent, capsules)
    options = variant.options(args)
    if not args.execute:
        print(json.dumps({
            "status": "frozen_parent_preflight_passed",
            "variant": variant.name, "revision": variant.revision,
            "parent_kind": registry["parent_kind"], "c_level": registry["c_level"],
            "parent_rerun": False,
            "question_count": registry["question_count"],
            "locked": len(registry["locked_question_ids"]),
            "targets": len(registry["target_question_ids"]),
            "triggered_capsules": len(capsules), "tasks": len(tasks),
            "max_agent_calls": len(capsules) * variant.max_calls_per_capsule,
            "finished_in_run_root": sum(finished_task(run_root, task) is not None for task in tasks),
            "model": model, "reasoning_effort": effort, "feedback_judge": args.feedback_judge,
            "workers": args.workers, "options": options,
            "run_root": str(run_root), "output": str(output),
        }, indent=2))
        return
    if not tasks:
        raise SystemExit("no triggered capsules: the parent locks every question")
    if not args.grader.is_file():
        raise SystemExit(f"no grader at {args.grader}; H needs it for in-run Yes/No")

    output.parent.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    oracle = FeedbackOracle(run_root, args.grader.resolve(), args.feedback_judge)
    # Freeze protocol and implementation before the first model call.
    sources = protocol_sources(variant)
    prompts = variant.prompts()
    protocol = {
        "revision": variant.revision, "variant": variant.name,
        "parent": {key: registry[key] for key in ("parent_kind", "c_level", "parent_generation_sha256",
                                                    "parent_scoring_sha256", "b_generation_sha256")},
        "triggered_capsules": [capsule["short_id"] for capsule in capsules],
        "tasks": [task["task_id"] for task in tasks],
        "workers": args.workers, "timeout_per_call": args.timeout,
        "model": model, "reasoning_effort": effort, "agent": agent,
        "feedback_oracle": oracle.manifest(), "options": options,
        "max_calls_per_capsule": variant.max_calls_per_capsule,
        "policy_retry": "fresh B heads keep B's single policy retry; repair stages never retry",
        "feedback": "per-question Yes/No only: parent scoring, then the sibling grader on frozen stage snapshots",
        "interpretation": "feedback-assisted developmental port of ngch D; not answer-blind",
        "prompt_sha256s": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in prompts.items()},
        "sources": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)} for p in sources]}
    write_json(run_root / "protocol.json", protocol)
    write_json(run_root / "prompt_snapshot.json", prompts)
    for path in sources:
        destination = run_root / "code_snapshot" / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    finished = {task["task_id"]: done for task in tasks if (done := finished_task(run_root, task)) is not None}
    progress_log(f"H/{variant.name}", f"capsules={len(capsules)} tasks={len(tasks)} finished={len(finished)} "
                 f"locked={len(registry['locked_question_ids'])} targets={len(registry['target_question_ids'])} "
                 f"workers={args.workers}")
    ctx = RunContext(run_root, args.timeout, oracle, parent, options)

    def run(task):
        if task["task_id"] in finished:
            return finished[task["task_id"]]
        # An unfinished task restarts from nothing; its partial workspaces are discarded.
        for directory in variant.task_dirs(task, run_root):
            shutil.rmtree(directory, ignore_errors=True)
        record = variant.run_task(task, ctx)
        write_json(task_record_path(run_root, task), record)
        progress_log(f"H/{variant.name}/{task['task_id']}", f"finished; agent calls={record.get('total_agent_calls', 0)}")
        return record

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.workers, len(tasks))) as pool:
        records = list(pool.map(run, tasks))
    final, replaced, selection = variant.select(records, ctx)
    for qid in registry["locked_question_ids"]:
        if final[qid] != parent.core[qid] or qid in replaced:
            raise RuntimeError(f"H changed a locked question: {qid}")
    for entry in protocol["sources"]:
        if digest(ROOT / entry["path"]) != entry["sha256"]:
            raise RuntimeError("protocol code changed during run")
    if digest(generation_path) != registry["parent_generation_sha256"]:
        raise RuntimeError("frozen parent generation changed during run")
    agent_calls = sum(record.get("total_agent_calls", 0) for record in records)
    usage: dict[str, float] = {}
    for record in records:
        for key, value in record.get("usage", {}).items():
            usage[key] = usage.get(key, 0) + value
    result = {
        "variant": variant.name,
        "experiment_revision": variant.revision,
        "protocol": protocol,
        "parent": registry,
        "locked_question_ids": registry["locked_question_ids"],
        "target_question_ids": registry["target_question_ids"],
        "triggered_capsules": capsules,
        "tasks": records,
        "selection": selection,
        "final_results": final,
        "h_replaced": replaced,
        "generation_summary": {
            "parent_rerun": False,
            "parent_correct": len(registry["locked_question_ids"]),
            "targets": len(registry["target_question_ids"]),
            "triggered_capsules": len(capsules),
            "tasks": len(tasks),
            "h_replaced_answers": len(replaced),
            "total_agent_calls": agent_calls,
            "feedback_oracle_calls": oracle.calls,
            "feedback_graded_answers": len(oracle.verdicts),
            "usage": usage,
            "wall_runtime_seconds": time.monotonic() - started,
            "resumed_finished_tasks": len(finished),
            "scorer_or_evaluator_used_during_generation": False,
            "feedback_assisted": True,
        },
        "freeze": {"status": "frozen_before_private_scoring",
                   "parent_generation_sha256": registry["parent_generation_sha256"],
                   "parent_scoring_sha256": registry["parent_scoring_sha256"],
                   "artifacts": freeze_run_root(run_root)},
    }
    write_json(output, result)
    report.write_text(
        f"# {variant.revision}: H rescue over a frozen {registry['parent_kind'].upper()} parent\n\n"
        f"Parent: {registry['parent_generation']}"
        + (f" (C level {registry['c_level']})" if registry["c_level"] else "") + ".\n\n"
        f"Parent correct (locked): {len(registry['locked_question_ids'])}/{registry['question_count']}; "
        f"targets: {len(registry['target_question_ids'])} in {len(capsules)} triggered capsules.\n\n"
        f"H replaced answers: {len(replaced)}; agent calls: {agent_calls}; "
        f"feedback grader runs: {oracle.calls}.\n\n"
        "The parent was not re-run. Score the export of this generation with the sibling grader.\n",
        encoding="utf-8",
    )
    print(json.dumps(result["generation_summary"], indent=2))
