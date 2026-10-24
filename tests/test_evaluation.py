from __future__ import annotations

import pytest

from orange_incident_investigation.dataset import INCIDENTS
from orange_incident_investigation.evaluation import (
    SEMANTIC_ROOT_CAUSE_RULES,
    _normalize_root_cause,
    _root_cause_exact_match,
    _semantic_root_cause_match,
    evaluate_systems,
)


def test_root_cause_exact_match_normalizes_case_punctuation_and_unicode_hyphens() -> None:
    assert _root_cause_exact_match(
        "Cache WARM\u2011up regression, in payment service!",
        "cache warm-up regression in payment service",
    ) == 1.0


def test_root_cause_exact_match_does_not_treat_longer_explanation_as_exact() -> None:
    assert _root_cause_exact_match(
        "Cache warm-up regression in payment service caused increased latency",
        "cache warm-up regression in payment service",
    ) == 0.0
    assert _normalize_root_cause("  API–Latency  ") == "api latency"


def test_semantic_rubric_matches_all_ground_truth_labels() -> None:
    assert set(SEMANTIC_ROOT_CAUSE_RULES) == {incident.incident_id for incident in INCIDENTS}
    for incident in INCIDENTS:
        assert _semantic_root_cause_match(incident.incident_id, incident.ground_truth) == 1.0


def test_semantic_rubric_requires_incident_specific_concepts() -> None:
    assert _semantic_root_cause_match(
        "INC-001",
        "Cache warm-up disabled in production caused a cache miss storm in the payment-service",
    ) == 1.0
    assert _semantic_root_cause_match(
        "INC-001",
        "Cache warm-up was disabled in the production rollout, causing a cache-miss storm "
        "and errors in payment-related endpoints",
    ) == 1.0
    assert _semantic_root_cause_match(
        "INC-002",
        "Search index restart loop caused shard health degradation and timeouts",
    ) == 0.0
    assert _semantic_root_cause_match("INC-002", "Search index restart loop queried stale shards") == 1.0
    assert _semantic_root_cause_match("INC-003", "Rate limiting misconfiguration on edge gateway throttled checkout") == 1.0
    assert _semantic_root_cause_match("INC-UNKNOWN", "cache warm-up regression in payment service") == 0.0


def test_semantic_accuracy_is_reported_separately_from_exact_accuracy() -> None:
    summary = evaluate_systems(INCIDENTS[:1])
    for system in ("multi_agent", "single_agent"):
        assert "average_root_cause_accuracy" in summary[system]
        assert "average_semantic_root_cause_accuracy" in summary[system]
        assert all(
            "semantic_root_cause_accuracy" in result
            for result in summary[system]["results"]
        )


def test_evaluation_summary_has_both_systems() -> None:
    summary = evaluate_systems()
    assert "multi_agent" in summary
    assert "single_agent" in summary
    assert summary["multi_agent"]["average_root_cause_accuracy"] >= 0.0
    assert summary["single_agent"]["average_root_cause_accuracy"] >= 0.0


def test_evaluation_can_run_a_small_incident_subset() -> None:
    summary = evaluate_systems(INCIDENTS[:1])

    assert summary["multi_agent"]["incident_count"] == 1
    assert len(summary["multi_agent"]["results"]) == 1
    assert summary["single_agent"]["incident_count"] == 1
    assert len(summary["single_agent"]["results"]) == 1


def test_evaluation_rejects_empty_incident_subset() -> None:
    with pytest.raises(ValueError, match="at least one incident"):
        evaluate_systems([])
