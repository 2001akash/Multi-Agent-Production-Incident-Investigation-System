from __future__ import annotations

import json
import importlib
from io import BytesIO
from typing import Any
from urllib.error import HTTPError

from orange_incident_investigation.agents import Orchestrator
from orange_incident_investigation.dataset import INCIDENTS
from orange_incident_investigation import llm as llm_module
from orange_incident_investigation.llm import GroqInvestigator
from orange_incident_investigation import main as main_module


def test_groq_request_uses_configured_key_and_counts_api_tokens(monkeypatch) -> None:
    llm = GroqInvestigator(api_key="test-key", model="test-model")
    response_body = json.dumps({
        "choices": [{"message": {"content": json.dumps({"tasks": ["logs"]})}}],
        "usage": {"total_tokens": 17},
    }).encode("utf-8")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            assert len(args) == 3
            return False

        def read(self) -> bytes:
            return response_body

    def fake_urlopen(request, timeout):
        assert request.full_url == llm_module.GROQ_CHAT_COMPLETIONS_URL
        assert request.get_header("Authorization") == "Bearer test-key"
        assert timeout == 45
        body = json.loads(request.data)
        assert body["model"] == "test-model"
        return FakeResponse()

    monkeypatch.setattr(llm_module, "urlopen", fake_urlopen)
    assert llm.plan_tasks("Elevated latency") == ["logs"]
    assert llm.total_tokens == 17


def test_shell_groq_key_takes_precedence_over_dotenv(monkeypatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "shell-key")
    monkeypatch.setenv("GROQ_MODEL", "shell-model")

    importlib.reload(llm_module)
    configured = llm_module.GroqInvestigator()

    assert configured.api_key == "shell-key"
    assert configured.model == "shell-model"


def test_task_router_filters_unsupported_tools(monkeypatch) -> None:
    llm = GroqInvestigator(api_key="test-key")
    monkeypatch.setattr(llm, "_complete_json", lambda *_: {"tasks": ["logs", "unsupported", "metrics"]})

    assert llm.plan_tasks("Elevated latency after a release") == ["logs", "metrics"]


def test_reason_requires_incident_specific_actions(monkeypatch) -> None:
    llm = GroqInvestigator(api_key="test-key")
    monkeypatch.setattr(llm, "_complete_json", lambda *_: {
        "ranked_hypotheses": [],
        "recommended_actions": [],
        "evidence_summary": "Evidence was inconclusive.",
        "assumptions": [],
        "inconclusive": True,
    })

    try:
        llm.reason("Elevated latency", [])
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected missing incident-specific actions to fail")

    assert "No generic fallback actions were substituted" in message


def test_reason_requires_non_empty_assumptions(monkeypatch) -> None:
    llm = GroqInvestigator(api_key="test-key")
    monkeypatch.setattr(llm, "_complete_json", lambda *_: {
        "ranked_hypotheses": [],
        "recommended_actions": ["Check the issuer URL in the migration configuration."],
        "evidence_summary": "The supplied evidence is inconclusive.",
        "assumptions": [],
        "inconclusive": True,
    })

    try:
        llm.reason("Login failures", [])
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected missing assumptions to fail")

    assert "non-empty assumptions" in message


def test_web_requires_groq_unless_offline_is_explicit(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("ORANGE_LLM_DISABLED", "1")
    monkeypatch.setattr("sys.argv", ["main", "--web"])

    try:
        main_module.main()
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected the web UI to require Groq by default")

    captured = capsys.readouterr()
    assert "--web requires GROQ_API_KEY" in captured.err


def test_forbidden_response_gives_safe_actionable_error(monkeypatch) -> None:
    secret = "test-api-secret"
    llm = GroqInvestigator(api_key=secret, model="test-model")
    body = json.dumps({
        "error": {"message": f"Access denied for {secret}; account is not entitled to this model"}
    }).encode("utf-8")

    def forbidden_urlopen(_request, timeout):
        assert timeout == 45
        raise HTTPError(
            llm_module.GROQ_CHAT_COMPLETIONS_URL,
            403,
            "Forbidden",
            None,
            BytesIO(body),
        )

    monkeypatch.setattr(llm_module, "urlopen", forbidden_urlopen)
    try:
        llm.plan_tasks("Elevated latency")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected an actionable Groq authorization error")

    assert "HTTP 403" in message
    assert "GROQ_API_KEY" not in message
    assert "[REDACTED]" in message
    assert secret not in message
    assert "account can use model 'test-model'" in message


def test_rate_limit_retries_after_provider_delay(monkeypatch) -> None:
    llm = GroqInvestigator(api_key="test-key", model="test-model")
    rate_limit_body = json.dumps({
        "error": {"message": "Rate limit reached. Please try again in 6.855s."}
    }).encode("utf-8")
    success_body = json.dumps({
        "choices": [{"message": {"content": json.dumps({"tasks": ["logs"]})}}],
        "usage": {"total_tokens": 10},
    }).encode("utf-8")
    response = type(
        "FakeResponse",
        (),
        {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *_args: False,
            "read": lambda self: success_body,
        },
    )()
    attempts = 0
    delays: list[float] = []

    def rate_limited_once(_request, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 45
        if attempts == 1:
            raise HTTPError(
                llm_module.GROQ_CHAT_COMPLETIONS_URL,
                429,
                "Too Many Requests",
                None,
                BytesIO(rate_limit_body),
            )
        return response

    monkeypatch.setattr(llm_module, "urlopen", rate_limited_once)
    monkeypatch.setattr(llm_module.time, "sleep", delays.append)

    assert llm.plan_tasks("Elevated latency") == ["logs"]
    assert attempts == 2
    assert delays == [6.855]
    assert llm.total_tokens == 10


def test_invalid_json_response_retries_with_stricter_prompt(monkeypatch) -> None:
    llm = GroqInvestigator(api_key="test-key", model="test-model")
    error_body = json.dumps({
        "error": {
            "message": "Failed to generate JSON. Please adjust your prompt.",
            "code": "failed_generation",
        }
    }).encode("utf-8")
    success_body = json.dumps({
        "choices": [{"message": {"content": json.dumps({"tasks": ["logs"]})}}],
        "usage": {"total_tokens": 12},
    }).encode("utf-8")
    attempts = 0

    class SuccessResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return success_body

    def fail_json_once(request, timeout):
        nonlocal attempts
        assert timeout == 45
        attempts += 1
        payload = json.loads(request.data)
        if attempts == 1:
            raise HTTPError(
                llm_module.GROQ_CHAT_COMPLETIONS_URL,
                400,
                "Bad Request",
                None,
                BytesIO(error_body),
            )
        assert "Retry requirement: return exactly one syntactically valid JSON object." in payload["messages"][0]["content"]
        return SuccessResponse()

    monkeypatch.setattr(llm_module, "urlopen", fail_json_once)

    assert llm.plan_tasks("Elevated latency") == ["logs"]
    assert attempts == 2
    assert llm.total_tokens == 12


def test_persistent_failed_generation_returns_actionable_error(monkeypatch) -> None:
    llm = GroqInvestigator(api_key="test-key", model="test-model")
    error_body = json.dumps({
        "error": {
            "message": "Failed to generate JSON. Please adjust your prompt.",
            "code": "failed_generation",
        }
    }).encode("utf-8")

    def always_fail(_request, timeout):
        assert timeout == 45
        raise HTTPError(
            llm_module.GROQ_CHAT_COMPLETIONS_URL,
            400,
            "Bad Request",
            None,
            BytesIO(error_body),
        )

    monkeypatch.setattr(llm_module, "urlopen", always_fail)

    try:
        llm.plan_tasks("Elevated latency")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected a JSON-generation error")

    assert "HTTP 400" in message
    assert "after one retry" in message
    assert "reduce the evidence payload" in message


def test_cli_shows_concise_groq_error_without_traceback(monkeypatch, capsys) -> None:
    class FailingOrchestrator:
        def __init__(self, _incident):
            pass

        def investigate(self):
            raise RuntimeError("Groq API returned HTTP 403. Check account and model access.")

    monkeypatch.setattr(main_module, "Orchestrator", FailingOrchestrator)
    monkeypatch.setattr("sys.argv", ["main", "--demo"])

    try:
        main_module.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected CLI to exit with an error code")

    captured = capsys.readouterr()
    assert "Error: Groq API returned HTTP 403" in captured.err
    assert "Traceback" not in captured.err


def test_cli_offline_option_runs_fallback_with_key_configured(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("ORANGE_LLM_DISABLED", raising=False)
    monkeypatch.setattr("sys.argv", ["main", "--demo", "--offline"])

    main_module.main()
    captured = capsys.readouterr()

    assert '"llm_provider": "local deterministic fallback"' in captured.out
    assert "test-key" not in captured.out


def test_orchestrator_routes_follow_up_without_sending_evaluation_label() -> None:
    incident = INCIDENTS[0]

    class FakeGroq:
        enabled = True
        model = "test-model"
        total_tokens = 0

        def __init__(self):
            self.received_report = ""
            self.reason_calls = 0

        def plan_tasks(self, report: str) -> list[str]:
            self.received_report = report
            return ["logs"]

        def reason(self, report: str, evidence: list[dict[str, str]]) -> dict[str, Any]:
            self.received_report = report
            self.reason_calls += 1
            return {
                "ranked_hypotheses": [{
                    "cause": "cache behavior changed after release",
                    "confidence": 0.8,
                    "support": [evidence[0]["summary"]],
                    "contradictions": [],
                }],
                "facts": [evidence[0]["summary"]],
                "assumptions": ["The observed cache change is related to the recent release."],
                "recommended_actions": ["Review the cache configuration change."],
                "inconclusive": False,
                "evidence_summary": "The hypothesis is supported by the retrieved log entry.",
            }

        def critique(
            self,
            _report: str,
            _hypotheses: list[dict[str, Any]],
            _evidence: list[dict[str, str]],
            available_tasks: list[str],
        ) -> dict[str, Any]:
            assert _report == incident.report
            assert _hypotheses
            assert _evidence
            assert "deployments" in available_tasks
            return {
                "needs_more_evidence": True,
                "tasks": ["deployments", "not-a-tool"],
                "question": "Did a deployment change cache behavior?",
            }

    fake_groq = FakeGroq()
    orchestrator = Orchestrator(incident, llm=fake_groq)
    report = orchestrator.investigate()

    assert orchestrator.tool_call_count == 2
    assert set(orchestrator.state.completed_tasks) == {"logs", "deployments"}
    assert fake_groq.reason_calls == 2
    assert incident.ground_truth not in fake_groq.received_report
    assert report.llm_provider == "Groq"
    assert report.llm_model == "test-model"
