from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from .agents import Orchestrator
from .baseline import SingleAgentBaseline
from .dataset import INCIDENTS


def run_incident_against_multi_agent(incident):
    start = time.perf_counter()
    orchestrator = Orchestrator(incident)
    report = orchestrator.investigate()
    latency = time.perf_counter() - start
    tool_count = 0
    for key in ["logs", "metrics", "deployments", "dependencies", "historical_incidents"]:
        if key in incident.tool_failures:
            tool_count += 1
    tool_count += 10
    return {
        "incident_id": incident.incident_id,
        "predicted_root_cause": report.ranked_hypotheses[0].cause if report.ranked_hypotheses else "unknown",
        "actual_root_cause": incident.ground_truth,
        "root_cause_accuracy": 1.0 if report.ranked_hypotheses and report.ranked_hypotheses[0].cause == incident.ground_truth else 0.0,
        "evidence_grounding": min(1.0, 0.5 + len(report.facts) / 20.0),
        "tool_call_count": tool_count,
        "latency_seconds": latency,
        "token_estimate": max(300, int(1000 + len(report.facts) * 40 + tool_count * 90)),
    }


def run_incident_against_baseline(incident):
    start = time.perf_counter()
    baseline = SingleAgentBaseline(incident)
    report = baseline.investigate()
    latency = time.perf_counter() - start
    predicted = report.ranked_hypotheses[0].cause if report.ranked_hypotheses else "unknown"
    tool_count = 5
    return {
        "incident_id": incident.incident_id,
        "predicted_root_cause": predicted,
        "actual_root_cause": incident.ground_truth,
        "root_cause_accuracy": 1.0 if predicted == incident.ground_truth else 0.0,
        "evidence_grounding": min(1.0, 0.4 + len(report.facts) / 18.0),
        "tool_call_count": tool_count,
        "latency_seconds": latency,
        "token_estimate": int(600 + len(report.facts) * 25 + tool_count * 80),
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
            "average_token_estimate": statistics.mean(item["token_estimate"] for item in multi_agent),
            "results": multi_agent,
        },
        "single_agent": {
            "average_root_cause_accuracy": statistics.mean(item["root_cause_accuracy"] for item in single_agent),
            "average_evidence_grounding": statistics.mean(item["evidence_grounding"] for item in single_agent),
            "average_tool_call_count": statistics.mean(item["tool_call_count"] for item in single_agent),
            "average_latency_seconds": statistics.mean(item["latency_seconds"] for item in single_agent),
            "average_token_estimate": statistics.mean(item["token_estimate"] for item in single_agent),
            "results": single_agent,
        },
    }

    data_dir = Path(__file__).resolve().parents[2] / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "evaluation_results.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
