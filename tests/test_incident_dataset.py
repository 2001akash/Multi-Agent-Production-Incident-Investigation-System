from __future__ import annotations

from orange_incident_investigation.dataset import INCIDENTS
from orange_incident_investigation.tools import ToolRegistry


def test_dataset_has_ten_incidents() -> None:
    assert len(INCIDENTS) == 10


def test_incident_ground_truth_is_present() -> None:
    for incident in INCIDENTS:
        assert incident.ground_truth
        assert incident.ground_truth in incident.possible_causes


def test_tool_registry_handles_failures() -> None:
    incident = INCIDENTS[0]
    registry = ToolRegistry(incident)
    result = registry.query_logs()
    assert result.success is True

    incident_with_failure = INCIDENTS[2]
    registry = ToolRegistry(incident_with_failure)
    result = registry.query_deployments()
    assert result.success is False
