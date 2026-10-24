from __future__ import annotations

import argparse
import json
from pathlib import Path

from .agents import Orchestrator
from .dataset import INCIDENTS
from .evaluation import evaluate_systems
from .web import run_web_server


def run_demo() -> None:
    incident = INCIDENTS[0]
    report = Orchestrator(incident).investigate()
    print(json.dumps({
        "incident_id": report.incident_id,
        "confidence": round(report.confidence, 3),
        "inconclusive": report.inconclusive,
        "ranked_hypotheses": [
            {"cause": h.cause, "confidence": round(h.confidence, 3), "evidence_count": h.evidence_count}
            for h in report.ranked_hypotheses
        ],
        "recommended_actions": report.recommended_actions,
        "notes": report.evidence_summary,
    }, indent=2))


def run_evaluation() -> None:
    summary = evaluate_systems()
    multi = summary["multi_agent"]
    single = summary["single_agent"]
    print("Evaluation comparison")
    print("=" * 80)
    print(f"Multi-agent accuracy: {multi['average_root_cause_accuracy']:.3f}")
    print(f"Single-agent accuracy: {single['average_root_cause_accuracy']:.3f}")
    print(f"Multi-agent grounding: {multi['average_evidence_grounding']:.3f}")
    print(f"Single-agent grounding: {single['average_evidence_grounding']:.3f}")
    print(f"Multi-agent average tool calls: {multi['average_tool_call_count']:.2f}")
    print(f"Single-agent average tool calls: {single['average_tool_call_count']:.2f}")
    print(f"Multi-agent latency (s): {multi['average_latency_seconds']:.3f}")
    print(f"Single-agent latency (s): {single['average_latency_seconds']:.3f}")
    print(f"Multi-agent tokens: {multi['average_token_estimate']:.0f}")
    print(f"Single-agent tokens: {single['average_token_estimate']:.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-agent production incident investigation system")
    parser.add_argument("--demo", action="store_true", help="Run a demo incident investigation")
    parser.add_argument("--evaluate", action="store_true", help="Run the synthetic evaluation")
    parser.add_argument("--web", action="store_true", help="Start the local web interface")
    parser.add_argument("--host", default="127.0.0.1", help="Web interface bind address")
    parser.add_argument("--port", type=int, default=8000, help="Web interface port")
    args = parser.parse_args()

    if args.web:
        run_web_server(host=args.host, port=args.port)
    elif args.demo:
        run_demo()
    elif args.evaluate:
        run_evaluation()
    else:
        run_demo()


if __name__ == "__main__":
    main()
