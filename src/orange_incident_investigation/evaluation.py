from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .agents import Orchestrator
from .baseline import SingleAgentBaseline
from .dataset import INCIDENTS


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
        "root_cause_accuracy": 1.0 if report.ranked_hypotheses and report.ranked_hypotheses[0].cause == incident.ground_truth else 0.0,
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
        "root_cause_accuracy": 1.0 if predicted == incident.ground_truth else 0.0,
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


def evaluate_systems() -> dict[str, object]:
    multi_agent = [run_incident_against_multi_agent(c) for c in INCIDENTS]
    single_agent = [run_incident_against_baseline(c) for c in INCIDENTS]

    summary = {
        "multi_agent": {
            "average_root_cause_accuracy": statistics.mean(item["root_cause_accuracy"] for item in multi_agent),
            "average_evidence_grounding": statistics.mean(item["evidence_grounding"] for item in multi_agent),
            "average_tool_call_count": statistics.mean(item["tool_call_count"] for item in multi_agent),
            "average_latency_seconds": statistics.mean(item["latency_seconds"] for item in multi_agent),
            "average_token_count": statistics.mean(item["token_count"] for item in multi_agent),
            "token_count_source": multi_agent[0]["token_count_source"],
            "llm_model": multi_agent[0]["llm_model"],
            "results": multi_agent,
        },
        "single_agent": {
            "average_root_cause_accuracy": statistics.mean(item["root_cause_accuracy"] for item in single_agent),
            "average_evidence_grounding": statistics.mean(item["evidence_grounding"] for item in single_agent),
            "average_tool_call_count": statistics.mean(item["tool_call_count"] for item in single_agent),
            "average_latency_seconds": statistics.mean(item["latency_seconds"] for item in single_agent),
            "average_token_count": statistics.mean(item["token_count"] for item in single_agent),
            "token_count_source": single_agent[0]["token_count_source"],
            "llm_model": single_agent[0]["llm_model"],
            "results": single_agent,
        },
    }

    data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "evaluation_results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
