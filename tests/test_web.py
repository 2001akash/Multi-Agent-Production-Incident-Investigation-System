from __future__ import annotations

from dataclasses import replace

import pytest

from orange_incident_investigation.agents import Orchestrator
from orange_incident_investigation.baseline import SingleAgentBaseline
from orange_incident_investigation.dataset import INCIDENTS
from orange_incident_investigation.web import _serialize_report


def test_report_endpoint_serializes_structured_report() -> None:
    report = _serialize_report("INC-001")
    assert report["incident_id"] == "INC-001"
    assert report["ranked_hypotheses"]
    assert report["recommended_actions"]


def test_unknown_incident_is_rejected() -> None:
    with pytest.raises(KeyError):
        _serialize_report("INC-UNKNOWN")


def test_evaluation_truth_does_not_influence_hypothesis_ranking() -> None:
    incident = INCIDENTS[0]
    changed_truth = replace(incident, ground_truth=incident.possible_causes[-1])

    multi_original = Orchestrator(incident).investigate()
    multi_changed = Orchestrator(changed_truth).investigate()
    baseline_original = SingleAgentBaseline(incident).investigate()
    baseline_changed = SingleAgentBaseline(changed_truth).investigate()

    assert [h.cause for h in multi_original.ranked_hypotheses] == [
        h.cause for h in multi_changed.ranked_hypotheses
    ]
    assert [h.cause for h in baseline_original.ranked_hypotheses] == [
        h.cause for h in baseline_changed.ranked_hypotheses
    ]
