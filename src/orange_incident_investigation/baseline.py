from __future__ import annotations

from .agents import ReasoningAgent, ReportingAgent, SharedInvestigationState
from .data_models import Evidence, IncidentCase, InvestigationReport
from .llm import GroqInvestigator
from .tools import ToolRegistry, parse_tool_result


class SingleAgentBaseline:
    def __init__(self, incident: IncidentCase, llm: GroqInvestigator | None = None):
        self.incident = incident
        self.tools = ToolRegistry(incident)
        self.llm = llm or GroqInvestigator()
        self.state = SharedInvestigationState()

    def investigate(self) -> InvestigationReport:
        tool_results = self.tools.query_all()
        self.state = SharedInvestigationState()
        all_lines: list[str] = []
        for name, result in tool_results.items():
            all_lines.extend(parse_tool_result(result))
            self.state.evidence.extend(
                Evidence(source=name, kind="signal", summary=sentence, weight=1.0)
                for sentence in parse_tool_result(result)
            )

        self.state.hypotheses = ReasoningAgent(self.incident, self.state, self.llm).hypothesize()
        report = ReportingAgent(self.incident, self.state).compose()
        report.evidence_summary = (
            f"Single-agent baseline analyzed all available tool evidence in one pass ({len(all_lines)} signal lines)."
        )
        return report
