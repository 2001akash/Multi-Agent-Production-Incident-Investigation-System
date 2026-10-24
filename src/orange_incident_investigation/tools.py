from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .data_models import IncidentCase, ToolResult


class ToolRegistry:
    def __init__(self, incident: IncidentCase):
        self.incident = incident

    def _fail_if_needed(self, tool_name: str) -> None:
        if self.incident.tool_failures.get(tool_name) == "timeout":
            raise TimeoutError(f"{tool_name} timed out")
        if self.incident.tool_failures.get(tool_name) == "incomplete":
            raise ValueError(f"{tool_name} returned incomplete data")

    def query_logs(self) -> ToolResult:
        try:
            self._fail_if_needed("logs")
            return ToolResult(
                tool_name="query_logs",
                success=True,
                payload={"entries": self.incident.logs},
            )
        except (TimeoutError, ValueError) as exc:
            return ToolResult(tool_name="query_logs", success=False, payload={}, error=str(exc))

    def query_metrics(self) -> ToolResult:
        try:
            self._fail_if_needed("metrics")
            return ToolResult(
                tool_name="query_metrics",
                success=True,
                payload={"entries": self.incident.metrics},
            )
        except (TimeoutError, ValueError) as exc:
            return ToolResult(tool_name="query_metrics", success=False, payload={}, error=str(exc))

    def query_deployments(self) -> ToolResult:
        try:
            self._fail_if_needed("deployments")
            return ToolResult(
                tool_name="query_deployments",
                success=True,
                payload={"entries": self.incident.deployments},
            )
        except (TimeoutError, ValueError) as exc:
            return ToolResult(tool_name="query_deployments", success=False, payload={}, error=str(exc))

    def query_dependencies(self) -> ToolResult:
        try:
            self._fail_if_needed("dependencies")
            return ToolResult(
                tool_name="query_dependencies",
                success=True,
                payload={"entries": self.incident.dependencies},
            )
        except (TimeoutError, ValueError) as exc:
            return ToolResult(tool_name="query_dependencies", success=False, payload={}, error=str(exc))

    def query_historical_incidents(self) -> ToolResult:
        try:
            self._fail_if_needed("historical_incidents")
            return ToolResult(
                tool_name="query_historical_incidents",
                success=True,
                payload={"entries": self.incident.historical_incidents},
            )
        except (TimeoutError, ValueError) as exc:
            return ToolResult(tool_name="query_historical_incidents", success=False, payload={}, error=str(exc))

    def query_all(self) -> dict[str, ToolResult]:
        return {
            "logs": self.query_logs(),
            "metrics": self.query_metrics(),
            "deployments": self.query_deployments(),
            "dependencies": self.query_dependencies(),
            "historical_incidents": self.query_historical_incidents(),
        }


def parse_tool_result(tool_result: ToolResult) -> list[str]:
    if not tool_result.success:
        return [f"Tool failure: {tool_result.tool_name}: {tool_result.error}"]
    return [str(item) for item in tool_result.payload.get("entries", [])]
