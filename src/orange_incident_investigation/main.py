from __future__ import annotations

import argparse
import json
import os
import sys

from .agents import Orchestrator
from .dataset import INCIDENTS
from .evaluation import evaluate_systems
from .web import run_web_server


def run_demo() -> None:
    incident = INCIDENTS[0]
    report = Orchestrator(incident).investigate()
    print(json.dumps({
        "incident_id": report.incident_id,
        "llm_provider": report.llm_provider,
        "llm_model": report.llm_model,
        "confidence": round(report.confidence, 3),
        "inconclusive": report.inconclusive,
        "ranked_hypotheses": [
            {"cause": h.cause, "confidence": round(h.confidence, 3), "evidence_count": h.evidence_count}
            for h in report.ranked_hypotheses
        ],
        "recommended_actions": report.recommended_actions,
        "notes": report.evidence_summary,
    }, indent=2))


def run_evaluation(limit: int | None = None) -> None:
    incidents = INCIDENTS if limit is None else INCIDENTS[:limit]
    summary = evaluate_systems(incidents)
    multi = summary["multi_agent"]
    single = summary["single_agent"]
    print("Evaluation comparison")
    print("=" * 80)
    print(f"Incidents evaluated: {len(incidents)} of {len(INCIDENTS)}")
    print(f"Multi-agent accuracy: {multi['average_root_cause_accuracy']:.3f}")
    print(f"Single-agent accuracy: {single['average_root_cause_accuracy']:.3f}")
    print(f"Multi-agent semantic accuracy: {multi['average_semantic_root_cause_accuracy']:.3f}")
    print(f"Single-agent semantic accuracy: {single['average_semantic_root_cause_accuracy']:.3f}")
    print(f"Multi-agent grounding: {multi['average_evidence_grounding']:.3f}")
    print(f"Single-agent grounding: {single['average_evidence_grounding']:.3f}")
    print(f"Multi-agent average tool calls: {multi['average_tool_call_count']:.2f}")
    print(f"Single-agent average tool calls: {single['average_tool_call_count']:.2f}")
    print(f"Multi-agent latency (s): {multi['average_latency_seconds']:.3f}")
    print(f"Single-agent latency (s): {single['average_latency_seconds']:.3f}")
    print(f"Multi-agent tokens ({multi['token_count_source']}): {multi['average_token_count']:.0f}")
    print(f"Single-agent tokens ({single['token_count_source']}): {single['average_token_count']:.0f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-agent production incident investigation system")
    parser.add_argument("--demo", action="store_true", help="Run a demo incident investigation")
    parser.add_argument("--evaluate", action="store_true", help="Run the synthetic evaluation")
    parser.add_argument(
        "--limit",
        type=int,
        help="Evaluate only the first N synthetic incidents (useful for live Groq runs)",
    )
    parser.add_argument("--web", action="store_true", help="Start the local web interface")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Disable Groq and use the local deterministic fallback",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Web interface bind address")
    parser.add_argument("--port", type=int, default=8000, help="Web interface port")
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= len(INCIDENTS):
        parser.error(f"--limit must be between 1 and {len(INCIDENTS)}")
    if args.limit is not None and not args.evaluate:
        parser.error("--limit can only be used with --evaluate")
    if args.offline:
        os.environ["ORANGE_LLM_DISABLED"] = "1"
    elif args.web:
        os.environ.pop("ORANGE_LLM_DISABLED", None)
        if not os.getenv("GROQ_API_KEY", "").strip():
            parser.error(
                "--web requires GROQ_API_KEY for LLM-generated reports. "
                "Use --web --offline to explicitly run the deterministic fallback."
            )

    try:
        if args.web:
            run_web_server(host=args.host, port=args.port)
        elif args.demo:
            run_demo()
        elif args.evaluate:
            run_evaluation(args.limit)
        else:
            run_demo()
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
