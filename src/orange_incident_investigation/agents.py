from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .data_models import AgentOutput, Evidence, Hypothesis, IncidentCase, InvestigationReport
from .tools import ToolRegistry, parse_tool_result


class SharedInvestigationState:
    def __init__(self):
        self.evidence: list[Evidence] = []
        self.pending_tasks: list[str] = []
        self.completed_tasks: dict[str, Any] = {}
        self.open_questions: list[str] = []
        self.hypotheses: list[Hypothesis] = []


class Orchestrator:
    def __init__(self, incident: IncidentCase):
        self.incident = incident
        self.tools = ToolRegistry(incident)
        self.state = SharedInvestigationState()

    def plan(self) -> list[str]:
        return ["logs", "metrics", "deployments", "dependencies", "historical_incidents"]

    def execute_parallel(self, tasks: list[str]) -> dict[str, list[str]]:
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            futures = {
                task: pool.submit(self._run_task, task)
                for task in tasks
            }
            return {task: future.result() for task, future in futures.items()}

    def _run_task(self, task: str) -> list[str]:
        mapping = {
            "logs": self.tools.query_logs,
            "metrics": self.tools.query_metrics,
            "deployments": self.tools.query_deployments,
            "dependencies": self.tools.query_dependencies,
            "historical_incidents": self.tools.query_historical_incidents,
        }
        tool_result = mapping[task]()
        return parse_tool_result(tool_result)

    def investigate(self) -> InvestigationReport:
        tasks = self.plan()
        task_data = self.execute_parallel(tasks)

        observability = ObservabilityAgent(self.incident, task_data).analyze()
        dependency = DependencyAgent(self.incident, task_data).analyze()
        self.state.evidence.extend(observability.evidence)
        self.state.evidence.extend(dependency.evidence)

        for result in (observability, dependency):
            self.state.open_questions.extend(result.open_questions)

        reasoning = ReasoningAgent(self.incident, self.state).hypothesize()
        self.state.hypotheses = reasoning

        critic = CriticAgent(self.incident, self.state).review()
        if critic.status == "request_more_evidence":
            follow_up = ["logs", "metrics", "deployments", "dependencies"]
            next_data = self.execute_parallel(follow_up)
            self.state.evidence.extend(self._reconcile_evidence(next_data))
            reasoning = ReasoningAgent(self.incident, self.state).hypothesize()
            self.state.hypotheses = reasoning

        report = ReportingAgent(self.incident, self.state).compose()
        return report

    def _reconcile_evidence(self, extra_data: dict[str, list[str]]) -> list[Evidence]:
        evidence: list[Evidence] = []
        for source, items in extra_data.items():
            for item in items:
                evidence.append(Evidence(source=source, kind="follow-up", summary=item, weight=1.0))
        return evidence


class ObservabilityAgent:
    def __init__(self, incident: IncidentCase, source_data: dict[str, list[str]]):
        self.incident = incident
        self.source_data = source_data

    def analyze(self) -> AgentOutput:
        findings: list[str] = []
        evidence: list[Evidence] = []
        open_questions: list[str] = []
        for item in self.source_data.get("logs", []):
            findings.append(f"Log signal: {item}")
            evidence.append(Evidence(source="logs", kind="observation", summary=item, weight=1.2))
        for item in self.source_data.get("metrics", []):
            findings.append(f"Metric signal: {item}")
            evidence.append(Evidence(source="metrics", kind="observation", summary=item, weight=1.2))
        if not self.source_data.get("logs"):
            open_questions.append("No log signal was available; missing log traces may hide the trigger.")
        return AgentOutput(agent_name="observability", findings=findings, evidence=evidence, open_questions=open_questions)


class DependencyAgent:
    def __init__(self, incident: IncidentCase, source_data: dict[str, list[str]]):
        self.incident = incident
        self.source_data = source_data

    def analyze(self) -> AgentOutput:
        findings: list[str] = []
        evidence: list[Evidence] = []
        open_questions: list[str] = []
        for item in self.source_data.get("deployments", []):
            findings.append(f"Deployment signal: {item}")
            evidence.append(Evidence(source="deployments", kind="dependency", summary=item, weight=1.4))
        for item in self.source_data.get("dependencies", []):
            findings.append(f"Dependency signal: {item}")
            evidence.append(Evidence(source="dependencies", kind="dependency", summary=item, weight=1.3))
        for item in self.source_data.get("historical_incidents", []):
            findings.append(f"Historical signal: {item}")
            evidence.append(Evidence(source="historical_incidents", kind="history", summary=item, weight=1.1))
        if not self.source_data.get("deployments"):
            open_questions.append("Deployment metadata was incomplete; final cause remains dependent on delayed configuration data.")
        return AgentOutput(agent_name="dependency", findings=findings, evidence=evidence, open_questions=open_questions)


class ReasoningAgent:
    def __init__(self, incident: IncidentCase, state: SharedInvestigationState):
        self.incident = incident
        self.state = state

    def hypothesize(self) -> list[Hypothesis]:
        scored: dict[str, float] = {cause: 0.0 for cause in self.incident.possible_causes}
        for issue in self.state.evidence:
            summary = issue.summary.lower()
            for cause in self.incident.possible_causes:
                if cause.lower() in summary:
                    scored[cause] += issue.weight
                else:
                    for word in cause.lower().split():
                        if len(word) > 3 and word in summary:
                            scored[cause] += 0.25
        hypotheses = [
            Hypothesis(
                cause=cause,
                confidence=min(0.99, max(0.12, score / 8.0)),
                support=[e.summary for e in self.state.evidence if cause.lower() in e.summary.lower()][:3],
                contradictions=[e.summary for e in self.state.evidence if any(word in e.summary.lower() for word in ("false", "unrelated", "healthy", "stable"))][:2],
                evidence_count=len([e for e in self.state.evidence if cause.lower() in e.summary.lower()]),
            )
            for cause, score in sorted(scored.items(), key=lambda x: x[1], reverse=True)
        ]
        return hypotheses


class CriticAgent:
    def __init__(self, incident: IncidentCase, state: SharedInvestigationState):
        self.incident = incident
        self.state = state

    def review(self) -> AgentOutput:
        top_hypothesis = max(self.state.hypotheses, key=lambda h: h.confidence, default=None)
        if top_hypothesis is None:
            return AgentOutput(agent_name="critic", findings=["No hypotheses available"], evidence=[], open_questions=["Need more evidence before deciding."], status="request_more_evidence")
        if top_hypothesis.confidence < 0.6 or top_hypothesis.evidence_count < 2:
            return AgentOutput(agent_name="critic", findings=[f"Critic challenges {top_hypothesis.cause} due to low evidence confidence."], evidence=[], open_questions=["Require additional evidence from logs, metrics, or dependency checks."], status="request_more_evidence")
        return AgentOutput(agent_name="critic", findings=[f"The top hypothesis {top_hypothesis.cause} is plausible and has enough evidence."], evidence=[], open_questions=[], status="ok")


class ReportingAgent:
    def __init__(self, incident: IncidentCase, state: SharedInvestigationState):
        self.incident = incident
        self.state = state

    def compose(self) -> InvestigationReport:
        ranked = sorted(self.state.hypotheses, key=lambda h: h.confidence, reverse=True)
        top = ranked[0] if ranked else Hypothesis(cause="unknown", confidence=0.0, support=[], contradictions=[], evidence_count=0)
        facts = sorted({e.summary for e in self.state.evidence}, key=len)[:10]
        assumptions = [
            "The final conclusion reflects the strongest evidence collected in the investigation.",
            "Any missing or conflicting signals are recorded as uncertainty rather than treated as facts.",
        ]
        recommended_actions = [
            f"Validate the most likely cause: {top.cause}.",
            "Apply a rollback or guardrail if the change is recent.",
            "Add telemetry to detect recurrence before the next release.",
        ]
        inconclusive = top.cause == "unknown" or top.confidence < 0.5
        evidence_summary = (
            "The investigation integrated observability, dependency, and historical evidence to rank causes. "
            if not inconclusive else "Evidence was insufficient to form a high-confidence root cause."
        )
        return InvestigationReport(
            incident_id=self.incident.incident_id,
            ranked_hypotheses=ranked[:3],
            facts=facts,
            assumptions=assumptions,
            confidence=top.confidence,
            recommended_actions=recommended_actions,
            inconclusive=inconclusive,
            evidence_summary=evidence_summary,
        )
