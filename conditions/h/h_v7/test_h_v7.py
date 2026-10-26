# Path: conditions/h/h_v7/test_h_v7.py
# Purpose: h_v7: ngch D-v7 controller/contract/localizer invariants on the port, plus an offline end-to-end capsule run.
"""DV7Tests is ngch conditions/d/test_d_v7.py with imports and source paths pointed at this port."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common.integrity.isolation_audit import audit_command
from conditions.h.core.aggregation import _normalized
from conditions.h.core.test_support import ROOT, FrozenParent, ScriptedAgent, capsule_payload, run_main, sha
from conditions.h.h_v7.contracts import (
    contract_variants,
    explicit_integer_domain,
    parse_answer_contract,
    parse_protocol,
)
from conditions.h.h_v7.controller import CandidateRegistry, analyze_disagreement
from conditions.h.h_v7.localizer import localize_capsule
from conditions.h.h_v7.pipeline import LocalizedV7


def _meta(**updates):
    value = {
        "data_object": "counts.tsv", "sample_scope": "all samples",
        "observation_unit": "gene", "denominator": "all genes",
        "comparison": "A vs B", "method": "formula", "package": "base",
        "version": "1", "unit": "count", "scientific_hypothesis": "h1",
        "evidence": "analysis/scripts/a.py", "confidence": "high",
    }
    value.update(updates)
    return value


def _entry(qid: str, answer: str, head: str, **meta):
    return {
        "qid": qid, "normalized_answer": _normalized(answer), "head": head,
        "metadata": _meta(**meta),
    }


class DV7Tests(unittest.TestCase):
    def test_numeric_dedup_is_unit_safe(self) -> None:
        self.assertEqual(_normalized("13"), _normalized("13.0"))
        self.assertEqual(_normalized("13"), _normalized("1.3e1"))
        self.assertNotEqual(_normalized("13"), _normalized("13%"))
        self.assertNotEqual(_normalized("0.13"), _normalized("13%"))
        self.assertNotEqual(_normalized("13"), _normalized("13:1"))

    def test_explicit_contract_has_only_legal_precision(self) -> None:
        question = "What percentage is observed? Round to 1 decimal place."
        contract = parse_answer_contract(question)
        self.assertEqual(contract["requested_precision"], 1)
        self.assertEqual(contract_variants(question, "23.473684", contract), [
            ("explicit_1dp", "23.5")
        ])

    def test_protocol_parser_extracts_named_constraints(self) -> None:
        spec = parse_protocol(
            "Use DESeq2 v1.42 and BH adjusted p-value <= 0.05 for mouse genes."
        )
        self.assertEqual(spec["package"].casefold(), "deseq2")
        self.assertEqual(spec["release"], "1.42")
        self.assertTrue(spec["cutoff_inclusive"])
        self.assertEqual(spec["precision"], None)

    def test_bounded_domain_requires_public_proof(self) -> None:
        self.assertEqual(explicit_integer_domain("How many among the top 20 genes?"), (0, 20))
        self.assertIsNone(explicit_integer_domain("How many genes are significant?"))

    def test_registry_keeps_provenance_but_inherits_one_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = CandidateRegistry(Path(directory) / "candidate_registry.jsonl")
            result = {"question_id": "q1", "status": "resolved", "candidates": [], "summary": "x"}
            a = registry.register(
                qid="q1", answer="13", head="h1", stage="s", result=result,
                metadata=_meta(), contract_variant="raw",
            )
            registry.mark_equivalent("q1", a["normalized_answer"], "no")
            b = registry.register(
                qid="q1", answer="13.0", head="h2", stage="s", result=result,
                metadata=_meta(method="other"), contract_variant="raw",
            )
            self.assertEqual(len(registry.entries), 2)
            self.assertTrue(b["submitted"])
            self.assertEqual(b["verdict"], "no")
            events = [json.loads(line) for line in registry.path.read_text().splitlines()]
            self.assertEqual([event["registry_sequence"] for event in events], list(range(1, len(events) + 1)))
            self.assertEqual(events[0]["registry_event"], "candidate_registered")

    def test_registry_resume_preserves_prefix_and_pending_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate_registry.jsonl"
            result = {"question_id": "q1", "status": "resolved", "candidates": [], "summary": "x"}
            registry = CandidateRegistry(path)
            registry.register(qid="q1", answer="13", head="h1", stage="s", result=result,
                              metadata=_meta(), contract_variant="raw")
            prefix = path.read_bytes()
            resumed = CandidateRegistry.resume(path, {"q1": result})
            self.assertEqual(path.read_bytes(), prefix)
            self.assertEqual(resumed.pending("q1")[0]["answer"], "13")
            self.assertEqual(resumed.result(resumed.entries[0]["candidate_id"])["candidates"][0]["answer"], "13")
            resumed.register(qid="q1", answer="14", head="h2", stage="s2", result=result,
                             metadata=_meta(), contract_variant="raw")
            self.assertTrue(path.read_bytes().startswith(prefix))

    def test_isolation_allows_system_sink_but_rejects_shared_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.assertEqual(audit_command("find /usr/lib 2>/dev/null", workspace), [])
            finding = audit_command("curl x -o /tmp/reactome.json", workspace)
            self.assertEqual(finding[0]["verdict"], "fatal_shared_temp_access")
            self.assertEqual(audit_command(f"curl x -o {workspace}/tmp/reactome.json", workspace), [])

    def test_disagreement_distinguishes_cases(self) -> None:
        stable = analyze_disagreement([
            _entry("q1", "7", "h1", method="formula", scientific_hypothesis="h1"),
            _entry("q1", "7.0", "h2", method="package", scientific_hypothesis="h2"),
        ], ["q1"])
        self.assertEqual(
            stable["questions"]["q1"]["case"],
            "case_1_stable_result_independent_methods",
        )
        grounding = analyze_disagreement([
            _entry("q1", "7", "h1"),
            _entry("q1", "8", "h2", data_object="other.tsv"),
        ], ["q1"])
        self.assertEqual(grounding["questions"]["q1"]["case"], "case_3_grounding_disagreement")
        shared = analyze_disagreement([
            _entry("q1", "7", "h1"), _entry("q1", "7", "h2"), _entry("q1", "7", "h3"),
        ], ["q1"])
        self.assertEqual(shared["questions"]["q1"]["case"], "case_4_shared_upstream_assumption")

    def test_localizer_uses_sibling_yes_as_anchor(self) -> None:
        rows = [
            {"question_id": "q1", "question": "How many genes?", "capsule_uuid": "u"},
            {"question_id": "q2", "question": "What percentage?", "capsule_uuid": "u"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            grounding = {
                "questions": [
                    {"question_id": qid, "data_object": "x", "observation_unit": "gene",
                     "denominator_or_population": "all", "comparison": "A", "metric_or_outcome": "m",
                     "method_or_estimator": "count"}
                    for qid in ("q1", "q2")
                ]
            }
            path = workspace / "analysis/grounding.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(grounding))
            protocols = {"questions": {qid: parse_protocol(row["question"]) for qid, row in zip(("q1", "q2"), rows)}}
            contracts = {"questions": {qid: parse_answer_contract(row["question"]) for qid, row in zip(("q1", "q2"), rows)}}
            value = localize_capsule(
                rows=rows,
                feedback=[{"question_id": "q1", "feedback": "yes"}, {"question_id": "q2", "feedback": "no"}],
                workspace=workspace, protocol_spec=protocols, answer_contract=contracts,
            )
            self.assertEqual(value["failure_pattern"], "isolated_question_failure")
            self.assertEqual(value["question_diagnostics"]["q2"]["validated_by_sibling_yes"], ["q1"])
            self.assertIn("simple_baseline", value["recommended_heads"])

    def test_generic_runner_has_no_qid_answer_constants(self) -> None:
        source = (ROOT / "conditions/h/h_v7/pipeline.py").read_text(encoding="utf-8")
        self.assertNotRegex(source, r"if\s+qid\s*==\s*['\"]bix-")
        self.assertNotIn("fixed_value", source)

    def test_all_v7_prompts_require_structured_metadata(self) -> None:
        prompts = sorted((ROOT / "conditions/h/h_v7/prompts").glob("v7_*.md"))
        self.assertGreaterEqual(len(prompts), 8)
        for prompt in prompts:
            source = prompt.read_text(encoding="utf-8")
            self.assertIn("candidate_metadata.json", source, prompt.name)
            self.assertIn("scientific_hypothesis", source, prompt.name)



# q2 ("median expression") is an isolated failure, so the localizer routes method_triangulation and simple_baseline.
ANSWERS = {"method_triangulation": "right", "simple_baseline": "other"}


def script(label, workspace):
    _, head, stage, _ = label.split("/")
    assert stage == "localized_multihead", label
    assert {"error_localization.json", "protocol_spec.json", "answer_contract.json", "feedback.json"} <= set(
        p.name for p in workspace.iterdir())
    return capsule_payload("bix-1", {"bix-1-q1": "changed", "bix-1-q2": ANSWERS[head]})


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.fixture = FrozenParent()

    def tearDown(self):
        self.fixture.cleanup()

    def test_localized_heads_then_ranked_submission_until_yes(self):
        agent = ScriptedAgent(script)
        summary = run_main(LocalizedV7(), self.fixture, "--execute", agent=agent)
        self.assertEqual(sorted(call["label"] for call in agent.calls),
                         ["H/method_triangulation/localized_multihead/bix-1",
                          "H/simple_baseline/localized_multihead/bix-1"])
        self.assertEqual((summary["h_replaced_answers"], summary["total_agent_calls"]), (1, 2))
        output = json.loads((self.fixture.base / "h_generation.json").read_text())
        self.assertEqual(output["h_replaced"], {"bix-1-q2": {"source": "localized_multihead",
                                                             "rule": "yes_no_submission"}})
        self.assertEqual(output["final_results"]["bix-1-q2"]["candidates"][0]["answer"], "right")
        self.assertEqual(output["final_results"]["bix-1-q1"]["candidates"][0]["answer"], "12")
        episode = output["tasks"][0]
        self.assertEqual(episode["error_localization"]["failure_pattern"], "isolated_question_failure")
        self.assertEqual(episode["accepted_stage"], {"bix-1-q1": "parent", "bix-1-q2": "localized_multihead"})
        # The parent's own No answer is never resubmitted; the best-ranked head answer goes first and wins.
        submitted = [(a["stage"], a["answer"], a["verdict"]) for a in episode["attempts"] if a["stage"] != "parent"]
        self.assertEqual(submitted, [("localized_multihead", "right", "yes")])
        metrics = {m["stage"]: m for m in output["selection"]["stage_metrics"]}
        self.assertEqual((metrics["parent"]["cumulative_accepted"], metrics["localized_multihead"]["newly_accepted"]),
                         (1, 1))
        capsule_dir = self.fixture.base / "h_run/capsules/bix-1__cap-1"
        self.assertTrue((capsule_dir / "analysis/error_localization.json").is_file())
        self.assertTrue((capsule_dir / "candidate_registry.jsonl").is_file())
        # Nothing is written into the frozen B workspace.
        b_workspace = self.fixture.base / "b/0000/bix-1__cap-1"
        self.assertEqual(sorted(p.name for p in (b_workspace / "analysis").iterdir()), ["grounding.json", "scripts"])
        for artifact in output["freeze"]["artifacts"]:
            self.assertEqual(sha(ROOT / artifact["path"]), artifact["sha256"])

    def test_preflight_reports_the_v7_call_bound(self):
        summary = run_main(LocalizedV7(), self.fixture, "--enable-bounded", "--ablation", "without_error_localizer")
        self.assertEqual((summary["tasks"], summary["max_agent_calls"]), (1, 9))
        self.assertEqual(summary["options"]["ablations"], ["without_error_localizer"])


if __name__ == "__main__":
    unittest.main()
