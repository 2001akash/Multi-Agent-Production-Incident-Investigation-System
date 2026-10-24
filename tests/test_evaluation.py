from __future__ import annotations

from orange_incident_investigation.evaluation import evaluate_systems


def test_evaluation_summary_has_both_systems() -> None:
    summary = evaluate_systems()
    assert "multi_agent" in summary
    assert "single_agent" in summary
    assert summary["multi_agent"]["average_root_cause_accuracy"] >= 0.0
    assert summary["single_agent"]["average_root_cause_accuracy"] >= 0.0
