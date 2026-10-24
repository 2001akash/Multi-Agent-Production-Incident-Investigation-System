from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

TASK_NAMES = (
    "logs",
    "metrics",
    "deployments",
    "dependencies",
    "historical_incidents",
)
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
MAX_RATE_LIMIT_RETRIES = 2
MAX_RATE_LIMIT_WAIT_SECONDS = 60
MAX_JSON_RETRIES = 1


class GroqInvestigator:
    """Small Groq chat-completions client for structured investigation decisions."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        explicit_api_key = api_key is not None
        self.api_key = (api_key if api_key is not None else os.getenv("GROQ_API_KEY", "")).strip()
        self.model = model or os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        disabled = os.getenv("ORANGE_LLM_DISABLED", "").lower() in {"1", "true", "yes"}
        self.enabled = bool(self.api_key) and (explicit_api_key or not disabled)
        self.total_tokens = 0

    def _complete_json(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Groq is not configured. Set GROQ_API_KEY in the project .env file.")

        request_body = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload)},
                ],
            }
        ).encode("utf-8")
        request = Request(
            GROQ_CHAT_COMPLETIONS_URL,
            data=request_body,
            headers={
    "Authorization": f"Bearer {self.api_key}",
    "Content-Type": "application/json",
    "User-Agent": "OrangeIncidentInvestigator/1.0",
}, 
            method="POST",
        )

        response_payload: Any = None
        rate_limit_retries = 0
        json_retries = 0
        while True:
            try:
                with urlopen(request, timeout=45) as response:
                    response_payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                provider_message = self._http_error_message(exc)
                if (
                    exc.code == 400
                    and json_retries < MAX_JSON_RETRIES
                    and self._is_json_generation_error(provider_message)
                ):
                    request_payload = json.loads(request.data.decode("utf-8"))
                    request_payload["messages"][0]["content"] += (
                        "\n\nRetry requirement: return exactly one syntactically valid JSON object. "
                        "Use double quotes for all JSON strings and keys. Do not include Markdown, "
                        "code fences, comments, or text before or after the JSON object."
                    )
                    request.data = json.dumps(request_payload).encode("utf-8")
                    json_retries += 1
                    continue
                if exc.code == 429 and rate_limit_retries < MAX_RATE_LIMIT_RETRIES:
                    retry_delay = self._retry_delay(exc, provider_message)
                    if retry_delay is not None and retry_delay <= MAX_RATE_LIMIT_WAIT_SECONDS:
                        time.sleep(retry_delay)
                        rate_limit_retries += 1
                        continue
                self._raise_http_error(exc, provider_message)
            except (URLError, TimeoutError) as exc:
                raise RuntimeError("Could not reach the Groq API. Check the network and retry.") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError("Groq returned a response that was not valid JSON.") from exc
        if not isinstance(response_payload, dict):
            raise RuntimeError("Groq response must be a JSON object.")

        try:
            content = response_payload["choices"][0]["message"]["content"]
            usage = response_payload.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Groq response was missing the expected chat completion fields.") from exc
        if not isinstance(content, str):
            raise RuntimeError("Groq response content was not text.")

        try:
            result = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Groq did not return the requested JSON object.") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Groq response must be a JSON object.")

        token_count = usage.get("total_tokens", 0)
        if isinstance(token_count, int) and token_count > 0:
            self.total_tokens += token_count
        return result

    def _raise_http_error(self, error: HTTPError, provider_message: str) -> None:
        if error.code == 401:
            guidance = "The API key is invalid or expired. Create a new Groq API key and update GROQ_API_KEY in .env."
        elif error.code == 403:
            guidance = (
                "Groq denied access. Check that the key belongs to an account with API access, and "
                f"that the account can use model '{self.model}'."
            )
        elif error.code == 404:
            guidance = f"Model '{self.model}' was not found. Set GROQ_MODEL to an active model ID."
        elif error.code == 429:
            guidance = "The Groq API rate or usage limit was reached. Wait for the quota window to reset and retry."
        elif error.code == 400 and self._is_json_generation_error(provider_message):
            guidance = (
                "Groq could not produce valid JSON after one retry. Try a shorter prompt, reduce the "
                "evidence payload, or select a model with reliable JSON-mode support."
            )
        else:
            guidance = "Check Groq API access, model configuration, and account limits."
        detail = f" Provider detail: {provider_message}" if provider_message else ""
        raise RuntimeError(f"Groq API returned HTTP {error.code}. {guidance}{detail}") from error

    def _retry_delay(self, error: HTTPError, provider_message: str) -> float | None:
        retry_after = error.headers.get("Retry-After") if error.headers else None
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        match = re.search(r"try again in\s*([0-9]+(?:\.[0-9]+)?)\s*s", provider_message, re.IGNORECASE)
        if match:
            return float(match.group(1))
        return None

    def _http_error_message(self, error: HTTPError) -> str:
        try:
            payload = json.loads(error.read(4096).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            return ""
        if not isinstance(payload, dict):
            return ""
        api_error = payload.get("error")
        if isinstance(api_error, dict):
            message = api_error.get("message")
        else:
            message = payload.get("message")
        if not isinstance(message, str):
            return ""
        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")
        return " ".join(message.split())[:300]

    @staticmethod
    def _is_json_generation_error(message: str) -> bool:
        lowered = message.lower()
        return "failed to generate json" in lowered or "failed_generation" in lowered

    def plan_tasks(self, incident_report: str) -> list[str]:
        result = self._complete_json(
            "You are an incident investigation orchestrator. Choose which independent evidence tools "
            "should run first. Return JSON only with a tasks array. Allowed tasks: logs, metrics, "
            "deployments, dependencies, historical_incidents. Do not infer a root cause.",
            {"incident_report": incident_report, "available_tasks": list(TASK_NAMES)},
        )
        tasks = result.get("tasks")
        if not isinstance(tasks, list):
            raise RuntimeError("Groq orchestrator response must include a tasks array.")
        selected = list(dict.fromkeys(task for task in tasks if isinstance(task, str) and task in TASK_NAMES))
        return selected or list(TASK_NAMES)

    def reason(self, incident_report: str, evidence: list[dict[str, str]]) -> dict[str, Any]:
        result = self._complete_json(
            "You are a senior production incident investigator. Analyze the incident using only the incident report and the supplied evidence. Never invent evidence or claim an unverified cause as fact. "

            "Return one valid JSON object containing these fields: "
            "ranked_hypotheses (array of objects with cause, confidence, support, contradictions), "
            "facts (array), assumptions (array), recommended_actions (array of 3 to 5 strings), "
            "inconclusive (boolean), and evidence_summary (string). "

            "CONFIDENCE AND EVIDENCE RULES: "
            "Each hypothesis must have a confidence between 0 and 1. "
            "Every support, contradiction, and fact must be copied exactly from a supplied evidence item's summary. "
            "Do not paraphrase, shorten, or invent evidence strings. "
            "Each hypothesis must have at least one directly relevant supporting evidence item. "
            "If no hypothesis has valid supporting evidence, return an empty ranked_hypotheses array rather than inventing support. "
            "Include only genuine contradictions; use an empty array when none exist. "
            "Never repeat the same evidence string within a hypothesis's support or contradictions. "

            "REPORTING RULES: "
            "Facts must be verified statements copied from the supplied evidence. "
            "Assumptions must be explicitly labelled uncertainties or unverified possibilities, not generic statements about the investigation process. "
            "Recommended actions must be specific, actionable, and relevant to this incident and its evidence. "
            "Do not use generic recommendations such as 'add telemetry' or 'apply a rollback' unless the evidence justifies them. "
            "The evidence summary must explain which signals support the leading hypothesis and what remains uncertain. "
            "If evidence is weak, conflicting, or incomplete, set inconclusive to true and explain what must be verified next. "
            "Do not leave required fields empty when relevant information is available.",
            {"incident_report": incident_report, "evidence": evidence},
        )
        if not isinstance(result.get("ranked_hypotheses"), list):
            raise RuntimeError("Groq reasoning response must include ranked_hypotheses.")
        actions = result.get("recommended_actions")
        if not isinstance(actions, list) or not any(
            isinstance(action, str) and action.strip() for action in actions
        ):
            raise RuntimeError(
                "Groq reasoning response must include incident-specific recommended_actions. "
                "No generic fallback actions were substituted."
            )
        summary = result.get("evidence_summary")
        if not isinstance(summary, str) or not summary.strip():
            raise RuntimeError("Groq reasoning response must include a non-empty evidence_summary.")
        assumptions = result.get("assumptions")
        if not isinstance(assumptions, list) or not any(
            isinstance(item, str) and item.strip() for item in assumptions
        ):
            raise RuntimeError("Groq reasoning response must include non-empty assumptions.")
        if not isinstance(result.get("inconclusive"), bool):
            raise RuntimeError("Groq reasoning response must include an inconclusive boolean.")
        return result

    def critique(
        self,
        incident_report: str,
        hypotheses: list[dict[str, Any]],
        evidence: list[dict[str, str]],
        available_tasks: list[str],
    ) -> dict[str, Any]:
        result = self._complete_json(
            "You are a skeptical incident critic. Check whether the leading hypothesis is actually "
            "supported, identify contradictions, and request targeted evidence only if needed. Return "
            "JSON with needs_more_evidence (boolean), tasks (array using only the provided available_tasks), "
            "and question (string). Do not claim a cause not supported by the supplied evidence.",
            {
                "incident_report": incident_report,
                "ranked_hypotheses": hypotheses,
                "evidence": evidence,
                "available_tasks": available_tasks,
            },
        )
        if not isinstance(result.get("needs_more_evidence"), bool):
            raise RuntimeError("Groq critic response must include needs_more_evidence as a boolean.")
        tasks = result.get("tasks", [])
        if not isinstance(tasks, list):
            raise RuntimeError("Groq critic response tasks must be an array.")
        result["tasks"] = list(dict.fromkeys(task for task in tasks if isinstance(task, str) and task in available_tasks))
        return result
