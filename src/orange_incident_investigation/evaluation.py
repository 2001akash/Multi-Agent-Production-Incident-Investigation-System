from __future__ import annotations

import json
import re
import statistics
import time
import unicodedata
from pathlib import Path
from collections.abc import Sequence

from .agents import Orchestrator
from .baseline import SingleAgentBaseline
from .data_models import IncidentCase
from .dataset import INCIDENTS


def _normalize_root_cause(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace("\u2011", "-").replace("\u2013", "-")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _root_cause_exact_match(predicted: str, actual: str) -> float:
    return float(_normalize_root_cause(predicted) == _normalize_root_cause(actual))


SEMANTIC_ROOT_CAUSE_RULES: dict[str, tuple[tuple[str, ...], ...]] = {
    "INC-001": (
        ("cache warm up",),
        ("payment service", "payment related", "payment related endpoints"),
        ("regression", "cache miss", "cache miss storm", "warm up disabled"),
    ),
    "INC-002": (
        ("search index", "search-index"),
        ("restart loop", "restart"),
        ("stale shards", "stale replicas", "stale replica"),
    ),
    "INC-003": (
    ("rate limit", "rate limiting"),
    ("edge gateway",),
    ("threshold", "too low", "misconfiguration", "throttled", "excessive 429"),
    ),
    "INC-004": (
        ("kafka consumer", "consumer lag"),
        ("deserializ", "schema change", "schema drift"),
        ("lag", "backlog", "offsets not advancing"),
    ),
    "INC-005": (
        ("issuer",),
        ("identity provider", "oidc", "jwt"),
        ("mismatch", "invalid", "incorrect", "stale"),
    ),
    "INC-006": (
        ("timezone", "time zone"),
        ("inventory sync", "inventory"),
        ("conversion", "utc"),
    ),
    "INC-007": (
        ("bulk export", "batch export", "export job"),
        ("queue worker", "workers", "sms worker"),
        ("starved", "cpu saturation", "cpu hit 99", "worker saturation"),
    ),
    "INC-008": (
        ("aggregator", "analytics agg"),
        ("timezone", "time zone", "dst", "cron"),
        ("missing partition", "skipped partition", "empty partition"),
    ),
    "INC-009": (
        ("feature flag",),
        ("segment key", "segment dimension", "targeting key"),
        ("wrong", "mismatch", "incorrect", "not mapped"),
    ),
    "INC-010": (
        ("query planner", "query plan", "planner regression"),
        ("index", "plan cache"),
        ("order status", "status table"),
    ),
}


def _semantic_root_cause_match(incident_id: str, predicted: str) -> float:
    rules = SEMANTIC_ROOT_CAUSE_RULES.get(incident_id)
    if not rules:
        return 0.0
    normalized_prediction = _normalize_root_cause(predicted)
    return float(
        all(
            any(_normalize_root_cause(term) in normalized_prediction for term in alternatives)
            for alternatives in rules
        )
    )


def _evidence_grounding(report, available_evidence: list[str]) -> float:
    citations = list(report.facts)
    for hypothesis in report.ranked_hypotheses:
        citations.extend(hypothesis.support)
        citations.extend(hypothesis.contradictions)
    if not citations:
        return 0.0
    evidence_set = set(available_evidence)
    grounded_count = sum(citation in evidence_set for citation in citations)
    return grounded_count / len(citations)


def run_incident_against_multi_agent(incident):
    start = time.perf_counter()
    orchestrator = Orchestrator(incident)
    report = orchestrator.investigate()
    latency = time.perf_counter() - start
    return {
        "incident_id": incident.incident_id,
        "predicted_root_cause": report.ranked_hypotheses[0].cause if report.ranked_hypotheses else "unknown",
        "actual_root_cause": incident.ground_truth,
        "root_cause_accuracy": _root_cause_exact_match(
            report.ranked_hypotheses[0].cause if report.ranked_hypotheses else "",
            incident.ground_truth,
        ),
        "semantic_root_cause_accuracy": _semantic_root_cause_match(
            incident.incident_id,
            report.ranked_hypotheses[0].cause if report.ranked_hypotheses else "",
        ),
        "evidence_grounding": _evidence_grounding(
            report,
            [item.summary for item in orchestrator.state.evidence],
        ),
        "tool_call_count": orchestrator.tool_call_count,
        "latency_seconds": latency,
        "token_count": orchestrator.llm.total_tokens,
        "token_count_source": "groq_api" if orchestrator.llm.enabled else "llm_disabled",
        "llm_model": orchestrator.llm.model if orchestrator.llm.enabled else None,
    }


def run_incident_against_baseline(incident):
    start = time.perf_counter()
    baseline = SingleAgentBaseline(incident)
    report = baseline.investigate()
    latency = time.perf_counter() - start
    predicted = report.ranked_hypotheses[0].cause if report.ranked_hypotheses else "unknown"
    return {
        "incident_id": incident.incident_id,
        "predicted_root_cause": predicted,
        "actual_root_cause": incident.ground_truth,
        "root_cause_accuracy": _root_cause_exact_match(predicted, incident.ground_truth),
        "semantic_root_cause_accuracy": _semantic_root_cause_match(incident.incident_id, predicted),
        "evidence_grounding": _evidence_grounding(
            report,
            [item.summary for item in baseline.state.evidence],
        ),
        "tool_call_count": 5,
        "latency_seconds": latency,
        "token_count": baseline.llm.total_tokens,
        "token_count_source": "groq_api" if baseline.llm.enabled else "llm_disabled",
        "llm_model": baseline.llm.model if baseline.llm.enabled else None,
    }


def evaluate_systems(incidents: Sequence[IncidentCase] = INCIDENTS) -> dict[str, object]:
    if not incidents:
        raise ValueError("Evaluation requires at least one incident.")
    multi_agent = [run_incident_against_multi_agent(incident) for incident in incidents]
    single_agent = [run_incident_against_baseline(incident) for incident in incidents]

    summary = {
        "multi_agent": {
            "average_root_cause_accuracy": statistics.mean(item["root_cause_accuracy"] for item in multi_agent),
            "average_semantic_root_cause_accuracy": statistics.mean(
                item["semantic_root_cause_accuracy"] for item in multi_agent
            ),
            "average_evidence_grounding": statistics.mean(item["evidence_grounding"] for item in multi_agent),
            "average_tool_call_count": statistics.mean(item["tool_call_count"] for item in multi_agent),
            "average_latency_seconds": statistics.mean(item["latency_seconds"] for item in multi_agent),
            "average_token_count": statistics.mean(item["token_count"] for item in multi_agent),
            "token_count_source": multi_agent[0]["token_count_source"],
            "llm_model": multi_agent[0]["llm_model"],
            "incident_count": len(incidents),
            "results": multi_agent,
        },
        "single_agent": {
            "average_root_cause_accuracy": statistics.mean(item["root_cause_accuracy"] for item in single_agent),
            "average_semantic_root_cause_accuracy": statistics.mean(
                item["semantic_root_cause_accuracy"] for item in single_agent
            ),
            "average_evidence_grounding": statistics.mean(item["evidence_grounding"] for item in single_agent),
            "average_tool_call_count": statistics.mean(item["tool_call_count"] for item in single_agent),
            "average_latency_seconds": statistics.mean(item["latency_seconds"] for item in single_agent),
            "average_token_count": statistics.mean(item["token_count"] for item in single_agent),
            "token_count_source": single_agent[0]["token_count_source"],
            "llm_model": single_agent[0]["llm_model"],
            "incident_count": len(incidents),
            "results": single_agent,
        },
    }

    data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "evaluation_results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
