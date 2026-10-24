from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class Evidence:
    source: str
    kind: str
    summary: str
    weight: float
    supports: str | None = None
    contradicts: str | None = None
    reliability: Literal["high", "medium", "low"] = "medium"


@dataclass
class Hypothesis:
    cause: str
    confidence: float
    support: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    evidence_count: int = 0


@dataclass
class IncidentCase:
    incident_id: str
    title: str
    report: str
    ground_truth: str
    possible_causes: list[str]
    logs: list[str]
    metrics: list[str]
    deployments: list[str]
    dependencies: list[str]
    historical_incidents: list[str]
    misleading_evidence: list[str]
    tool_failures: dict[str, str] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_name: str
    success: bool
    payload: dict[str, Any]
    error: str | None = None


@dataclass
class AgentOutput:
    agent_name: str
    findings: list[str]
    evidence: list[Evidence]
    open_questions: list[str]
    status: str = "ok"


@dataclass
class InvestigationReport:
    incident_id: str
    ranked_hypotheses: list[Hypothesis]
    facts: list[str]
    assumptions: list[str]
    confidence: float
    recommended_actions: list[str]
    inconclusive: bool = False
    evidence_summary: str = ""
