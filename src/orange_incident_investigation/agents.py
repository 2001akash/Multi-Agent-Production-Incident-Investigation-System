from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any, Protocol

from .data_models import AgentOutput, Evidence, Hypothesis, IncidentCase, InvestigationReport
from .llm import GroqInvestigator, TASK_NAMES
from .tools import ToolRegistry, parse_tool_result


class InvestigationLLM(Protocol):
    enabled: bool
    model: str
    total_tokens: int

    def plan_tasks(self, incident_report: str) -> list[str]: ...

    def reason(self, incident_report: str, evidence: list[dict[str, str]]) -> dict[str, Any]: ...

    def critique(
        self,
        incident_report: str,
        hypotheses: list[dict[str, Any]],
        evidence: list[dict[str, str]],
        available_tasks: list[str],
    ) -> dict[str, Any]: ...


class SharedInvestigationState:
    def __init__(self):
        self.evidence: list[Evidence] = []
        self.pending_tasks: list[str] = []
        self.completed_tasks: dict[str, Any] = {}
        self.open_questions: list[str] = []
        self.hypotheses: list[Hypothesis] = []
        self.reasoning_result: dict[str, Any] | None = None
        self.llm_model: str | None = None
        self.critic_question = ""
        self.follow_up_tasks: list[str] = []


class Orchestrator:
    def __init__(self, incident: IncidentCase, llm: InvestigationLLM | None = None):
        self.incident = incident
        self.tools = ToolRegistry(incident)
        self.state = SharedInvestigationState()
        self.llm = llm or GroqInvestigator()
        self.tool_call_count = 0

    def plan(self) -> list[str]:
        if self.llm.enabled:
            return self.llm.plan_tasks(self.incident.report)
        return list(TASK_NAMES)

    def execute_parallel(self, tasks: list[str]) -> dict[str, list[str]]:
        if not tasks:
            return {}
        self.tool_call_count += len(tasks)
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
        self.state.completed_tasks.update(task_data)

        observability = ObservabilityAgent(self.incident, task_data).analyze()
        dependency = DependencyAgent(self.incident, task_data).analyze()
        self.state.evidence.extend(observability.evidence)
        self.state.evidence.extend(dependency.evidence)

        for result in (observability, dependency):
            self.state.open_questions.extend(result.open_questions)

        reasoning = ReasoningAgent(self.incident, self.state, self.llm).hypothesize()
        self.state.hypotheses = reasoning

        critic = CriticAgent(self.incident, self.state, self.llm).review()
        if critic.status == "request_more_evidence" and self.state.follow_up_tasks:
            next_data = self.execute_parallel(self.state.follow_up_tasks)
            self.state.completed_tasks.update(next_data)
            self.state.evidence.extend(self._reconcile_evidence(next_data))
            reasoning = ReasoningAgent(self.incident, self.state, self.llm).hypothesize()
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
    def __init__(
        self,
        incident: IncidentCase,
        state: SharedInvestigationState,
        llm: InvestigationLLM | None = None,
    ):
        self.incident = incident
        self.state = state
        self.llm = llm

    def hypothesize(self) -> list[Hypothesis]:
        if self.llm and self.llm.enabled:
            result = self.llm.reason(
                self.incident.report,
                [{"source": item.source, "summary": item.summary} for item in self.state.evidence],
            )
            self.state.reasoning_result = result
            self.state.llm_model = self.llm.model
            allowed_evidence = {item.summary for item in self.state.evidence}
            hypotheses: list[Hypothesis] = []
            for item in result["ranked_hypotheses"]:
                if not isinstance(item, dict) or not isinstance(item.get("cause"), str):
                    continue
                raw_confidence = item.get("confidence", 0.0)
                if not isinstance(raw_confidence, (int, float)):
                    continue
                support = [summary for summary in _string_list(item.get("support")) if summary in allowed_evidence]
                contradictions = [
                    summary
                    for summary in _string_list(item.get("contradictions"))
                    if summary in allowed_evidence
                ]
                if not support:
                    continue
                hypotheses.append(
                    Hypothesis(
                        cause=item["cause"][:500],
                        confidence=min(1.0, max(0.0, float(raw_confidence))),
                        support=support,
                        contradictions=contradictions,
                        evidence_count=len(support),
                    )
                )
            if hypotheses:
                return hypotheses[:5]
            raise RuntimeError("Groq reasoning response did not cite any supplied evidence.")

        return self._evidence_derived_hypotheses()

    def _evidence_derived_hypotheses(self) -> list[Hypothesis]:
        evidence = self.state.evidence
        if not evidence:
            return []

        token_sets = [
            set(re.findall(r"[a-z0-9]{4,}", item.summary.lower()))
            for item in evidence
        ]
        hypotheses = []
        for index, item in enumerate(evidence):
            tokens = token_sets[index]
            related = [
                other
                for other_index, other in enumerate(evidence)
                if other_index != index and len(tokens & token_sets[other_index]) >= 2
            ]
            confidence = min(0.8, 0.3 + (0.12 * len(related)) + (0.05 if item.source in {"logs", "metrics"} else 0))
            contradictions = [
                other.summary
                for other in evidence
                if other.source != item.source
                and any(marker in other.summary.lower() for marker in ("healthy", "normal", "stable", "unrelated"))
            ][:2]
            hypotheses.append(
                Hypothesis(
                    cause=f"Potential cause suggested by {item.source}: {item.summary}",
                    confidence=confidence,
                    support=[item.summary, *(other.summary for other in related[:2])],
                    contradictions=contradictions,
                    evidence_count=1 + len(related),
                )
            )
        hypotheses.sort(key=lambda hypothesis: hypothesis.confidence, reverse=True)
        unique: list[Hypothesis] = []
        seen: set[str] = set()
        for hypothesis in hypotheses:
            if hypothesis.cause not in seen:
                seen.add(hypothesis.cause)
                unique.append(hypothesis)
            if len(unique) == 5:
                break
        return unique


class CriticAgent:
    def __init__(
        self,
        incident: IncidentCase,
        state: SharedInvestigationState,
        llm: InvestigationLLM | None = None,
    ):
        self.incident = incident
        self.state = state
        self.llm = llm

    def review(self) -> AgentOutput:
        top_hypothesis = max(self.state.hypotheses, key=lambda h: h.confidence, default=None)
        remaining_tasks = [task for task in TASK_NAMES if task not in self.state.completed_tasks]
        if self.llm and self.llm.enabled:
            critique = self.llm.critique(
                self.incident.report,
                [
                    {
                        "cause": hypothesis.cause,
                        "confidence": hypothesis.confidence,
                        "support": hypothesis.support,
                        "contradictions": hypothesis.contradictions,
                    }
                    for hypothesis in self.state.hypotheses
                ],
                [{"source": item.source, "summary": item.summary} for item in self.state.evidence],
                remaining_tasks,
            )
            self.state.critic_question = str(critique.get("question", ""))[:500]
            self.state.follow_up_tasks = [
                task for task in critique["tasks"] if task in remaining_tasks
            ][:len(remaining_tasks)]
            if critique["needs_more_evidence"] and self.state.follow_up_tasks:
                return AgentOutput(
                    agent_name="critic",
                    findings=["Critic requested targeted follow-up evidence."],
                    evidence=[],
                    open_questions=[self.state.critic_question] if self.state.critic_question else [],
                    status="request_more_evidence",
                )
            return AgentOutput(
                agent_name="critic",
                findings=["Critic reviewed the evidence and did not request more tool data."],
                evidence=[],
                open_questions=[],
                status="ok",
            )

        if top_hypothesis is None:
            self.state.follow_up_tasks = remaining_tasks
            return AgentOutput(agent_name="critic", findings=["No hypotheses available"], evidence=[], open_questions=["Need more evidence before deciding."], status="request_more_evidence")
        if top_hypothesis.confidence < 0.6 or top_hypothesis.evidence_count < 2:
            self.state.follow_up_tasks = remaining_tasks
            return AgentOutput(agent_name="critic", findings=[f"Critic challenges {top_hypothesis.cause} due to low evidence confidence."], evidence=[], open_questions=["Require additional evidence from logs, metrics, or dependency checks."], status="request_more_evidence")
        return AgentOutput(agent_name="critic", findings=[f"The top hypothesis {top_hypothesis.cause} is plausible and has enough evidence."], evidence=[], open_questions=[], status="ok")


class ReportingAgent:
    def __init__(self, incident: IncidentCase, state: SharedInvestigationState):
        self.incident = incident
        self.state = state

    def compose(self) -> InvestigationReport:
        ranked = (
            list(self.state.hypotheses)
            if self.state.reasoning_result is not None
            else sorted(self.state.hypotheses, key=lambda h: h.confidence, reverse=True)
        )
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
        if self.state.reasoning_result is not None:
            result = self.state.reasoning_result
            allowed_evidence = {item.summary for item in self.state.evidence}
            facts = [
                fact for fact in _string_list(result.get("facts"))
                if fact in allowed_evidence
            ][:10] or facts
            assumptions = _string_list(result.get("assumptions"))[:10] or assumptions
            recommended_actions = _string_list(result.get("recommended_actions"))[:10] or recommended_actions
            inconclusive = bool(result.get("inconclusive", inconclusive))
            evidence_summary = str(result.get("evidence_summary", evidence_summary))[:1000]
        return InvestigationReport(
            incident_id=self.incident.incident_id,
            ranked_hypotheses=ranked[:3],
            facts=facts,
            assumptions=assumptions,
            confidence=top.confidence,
            recommended_actions=recommended_actions,
            inconclusive=inconclusive,
            evidence_summary=evidence_summary,
            llm_provider="Groq" if self.state.reasoning_result is not None else "local deterministic fallback",
            llm_model=self.state.llm_model,
        )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item[:1000] for item in value if isinstance(item, str)]
