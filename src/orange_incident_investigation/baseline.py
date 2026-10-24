from __future__ import annotations

from .data_models import Evidence, IncidentCase, InvestigationReport, Hypothesis
from .tools import ToolRegistry, parse_tool_result


class SingleAgentBaseline:
    def __init__(self, incident: IncidentCase):
        self.incident = incident
        self.tools = ToolRegistry(incident)

    def investigate(self) -> InvestigationReport:
        tool_results = self.tools.query_all()
        all_lines = []
        for name, result in tool_results.items():
            all_lines.extend(parse_tool_result(result))

        score_by_cause: dict[str, float] = {cause: 0.0 for cause in self.incident.possible_causes}
        for sentence in all_lines:
            for cause in self.incident.possible_causes:
                if cause.lower() in sentence.lower():
                    score_by_cause[cause] += 1.5
                if cause.split(" ")[0].lower() in sentence.lower() or cause.split(" ")[-1].lower() in sentence.lower():
                    score_by_cause[cause] += 0.5

        ranked = sorted(score_by_cause.items(), key=lambda item: item[1], reverse=True)
        best_cause, best_score = ranked[0]
        confidence = min(0.96, max(0.2, best_score / 12.0))

        evidence = [
            Evidence(source="tool", kind="signal", summary=sentence, weight=1.0)
            for sentence in all_lines[:8]
        ]
        hypothesis = [Hypothesis(cause=best_cause, confidence=confidence, support=[sentence for sentence in all_lines[:3]], contradictions=self.incident.misleading_evidence[:1], evidence_count=len(all_lines))]

        recommended_actions = [
            f"Validate the top hypothesis: {best_cause}.",
            "Capture a focused reproduction to confirm the root cause.",
            "Apply a minimal rollback or guardrail before broader rollout.",
        ]

        return InvestigationReport(
            incident_id=self.incident.incident_id,
            ranked_hypotheses=hypothesis,
            facts=[sentence for sentence in all_lines[:5]],
            assumptions=["The observed signals are sufficient to identify the likeliest cause.", "No deeper dependency issue is yet isolated."],
            confidence=confidence,
            recommended_actions=recommended_actions,
            evidence_summary=f"Single-agent baseline analyzed {len(all_lines)} signal lines and selected {best_cause} as the likeliest cause.",
        )
