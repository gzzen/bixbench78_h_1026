# Path: conditions/h/h_v7/contracts.py
# Purpose: Deterministic protocol and answer-contract parsing for D-v7 (port of ngch conditions/d/modules/v7_contracts.py; only imports changed).
"""Deterministic protocol and answer-contract parsing for D-v7.

The parsers use only public question text.  They never inspect evaluator state,
reference answers, prior predictions, or historical workspaces.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from conditions.h.core.answer_contract import normalize_answer


PROTOCOL_FIELDS = (
    "package", "function", "organism", "database", "release",
    "identifier_namespace", "background", "cutoff", "cutoff_inclusive",
    "multiple_testing", "requested_field", "unit", "precision",
)


def _match(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    found = re.search(pattern, text, flags)
    return found.group(1).strip() if found else None


def parse_protocol(question: str) -> dict[str, Any]:
    """Extract literal named-protocol constraints without inferring an answer."""
    package = _match(
        r"\b(DESeq2|clusterProfiler|enrichGO|PhyKIT|ClipKIT|edgeR|limma|Seurat|"
        r"scanpy|statsmodels|scipy|bedtools|samtools|GATK|BLAST|IQ-TREE|MAFFT)\b",
        question,
    )
    function = _match(r"\b([A-Za-z][A-Za-z0-9_.]*(?:\([^)]*\)))(?:\s|,|\.|\?|$)", question)
    organism = _match(
        r"\b(human|mouse|rat|fung(?:us|al)|animal|yeast|Arabidopsis|"
        r"Homo sapiens|Mus musculus|Saccharomyces cerevisiae)\b",
        question,
    )
    database = _match(
        r"\b(GO(?:\s+(?:Process|Function|Component))?|KEGG(?:_[0-9]{4}_[A-Za-z]+)?|"
        r"Reactome|MSigDB|Gene Ontology|gencode|Enrichr)\b",
        question,
    )
    release = _match(r"\b(?:release|version|v)\s*([0-9]+(?:\.[0-9]+)*)\b", question)
    namespace = _match(r"\b(Entrez(?:\s+Gene)?|ENSEMBL|Ensembl|HGNC|UniProt|RefSeq)\b", question)
    background = None
    if re.search(r"\b(?:all genes|over all genes)\s+as\s+(?:the\s+)?background\b", question, re.I):
        background = "all genes"
    else:
        background = _match(r"\bbackground\s*(?:of|=|:)?\s*([^,;?.]+)", question)
    cutoff = _match(
        r"\b(?:p|p-value|padj|adjusted p(?:-value)?|similarity|VAF)\s*"
        r"(<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)?)",
        question,
    )
    cutoff_match = re.search(
        r"\b(p|p-value|padj|adjusted p(?:-value)?|similarity|VAF)\s*"
        r"(<=|>=|<|>)\s*([0-9]+(?:\.[0-9]+)?)",
        question,
        re.I,
    )
    if cutoff_match:
        cutoff = f"{cutoff_match.group(1)} {cutoff_match.group(2)} {cutoff_match.group(3)}"
        cutoff_inclusive: bool | None = "=" in cutoff_match.group(2)
    else:
        cutoff_inclusive = None
    multiple_testing = _match(
        r"\b(Benjamini[- ]Hochberg|BH|Bonferroni|FDR|adjusted p(?:-value)?|padj)\b",
        question,
    )
    requested_field = _match(
        r"\b(odds ratio|p-value|adjusted p-value|padj|correlation coefficient|"
        r"median|mean|percentage|proportion|count|pathway|gene set|statistic)\b",
        question,
    )
    unit = _match(r"\b(mm²|mm\^2|cm²|cm\^2|percent|percentage|%|bp|kb|Mb|fold)\b", question)
    precision = _match(
        r"\b(?:round(?:ed)?\s+to|to)\s+([0-9]+)\s+decimal\s+place",
        question,
    )
    if precision is None and re.search(r"\bnearest\s+integer\b|\bwhole\s+number\b", question, re.I):
        precision = "0"
    return {
        "package": package,
        "function": function,
        "organism": organism,
        "database": database,
        "release": release,
        "identifier_namespace": namespace,
        "background": background,
        "cutoff": cutoff,
        "cutoff_inclusive": cutoff_inclusive,
        "multiple_testing": multiple_testing,
        "requested_field": requested_field,
        "unit": unit,
        "precision": int(precision) if precision is not None else None,
    }


def protocol_is_named(spec: Mapping[str, Any]) -> bool:
    return any(spec.get(key) is not None for key in (
        "package", "function", "organism", "database", "release",
        "identifier_namespace", "background", "multiple_testing",
    ))


def parse_answer_contract(question: str) -> dict[str, Any]:
    q = question.casefold()
    if "percentage" in q or "percent" in q or "%" in question:
        answer_type = "percentage"
    elif "proportion" in q or "fraction" in q:
        answer_type = "proportion"
    elif "ratio" in q or "odds" in q:
        answer_type = "ratio"
    elif re.search(r"\bhow many\b|\bnumber of\b|\bcount\b", q):
        answer_type = "integer"
    elif re.search(r"\bp-?value\b|\bpadj\b", q):
        answer_type = "scientific_notation"
    elif re.search(r"\bdescribe how\b|\bdirection\b|\bincrease|decrease", q):
        answer_type = "direction"
    elif re.search(r"\bwhich (?:group|category|cell type|patient volume)\b", q):
        answer_type = "category"
    elif re.search(r"\bpathway|gene set|GO term\b", question, re.I):
        answer_type = "pathway_name"
    elif re.search(r"\bwhich gene\b|\bgene name\b", q):
        answer_type = "gene_name"
    else:
        answer_type = "decimal"

    precision_match = re.search(
        r"(?:round(?:ed)?\s+to|to)\s+([0-9]+)\s+decimal\s+place", question, re.I
    )
    precision = int(precision_match.group(1)) if precision_match else None
    rounding_rule = "round_half_even_unspecified" if precision is not None else None
    if answer_type == "integer" or re.search(r"nearest\s+integer|whole\s+number", q):
        precision = 0
        rounding_rule = "nearest_integer"
    unit = _match(r"\b(mm²|mm\^2|cm²|cm\^2|%|bp|kb|Mb|fold)\b", question)
    label_required = answer_type in {"category", "gene_name", "pathway_name", "direction"}
    return {
        "answer_type": answer_type,
        "unit": unit,
        "requested_precision": precision,
        "rounding_rule": rounding_rule,
        "label_required": label_required,
        "explicit_precision": precision is not None,
    }


def specs_for_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    protocols = {row["question_id"]: parse_protocol(str(row["question"])) for row in rows}
    contracts = {row["question_id"]: parse_answer_contract(str(row["question"])) for row in rows}
    return (
        {"questions": protocols, "source": "public-question deterministic parser v7"},
        {"questions": contracts, "source": "public-question deterministic parser v7"},
    )


def _numeric(value: str) -> Decimal | None:
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)\s*%?\s*", value, re.I)
    if not match:
        return None
    try:
        number = Decimal(match.group(1))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def contract_variants(question: str, answer: str, contract: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Return only question-legal deterministic formatting candidates."""
    answer = answer.strip()
    if not answer:
        return []
    precision = contract.get("requested_precision")
    number = _numeric(answer)
    if precision is not None and number is not None:
        suffix = "%" if contract.get("answer_type") == "percentage" and "%" in answer else ""
        value = f"{number:.{int(precision)}f}{suffix}"
        return [(f"explicit_{precision}dp", value)]
    variants: list[tuple[str, str]] = [("raw", answer)]
    if contract.get("answer_type") in {"percentage", "proportion", "ratio", "decimal", "scientific_notation"}:
        variants.extend((
            ("1dp", normalize_answer(question, answer, 1)),
            ("0dp", normalize_answer(question, answer, 0)),
        ))
    seen: set[str] = set()
    unique = []
    for label, value in variants:
        key = value.strip().casefold()
        if value.strip() and key not in seen:
            seen.add(key)
            unique.append((label, value.strip()))
    return unique


def explicit_integer_domain(question: str, maximum_size: int = 500) -> tuple[int, int] | None:
    """Infer a bounded domain only when the public wording proves the bound."""
    if parse_answer_contract(question)["answer_type"] != "integer":
        return None
    patterns = (
        r"among\s+(?:the\s+)?top\s+([0-9]+)",
        r"out\s+of\s+([0-9]+)",
        r"from\s+([0-9]+)\s+(?:members|items|genes|pathways)",
    )
    for pattern in patterns:
        match = re.search(pattern, question, re.I)
        if match:
            upper = int(match.group(1))
            if 0 <= upper <= maximum_size:
                return 0, upper
    return None
