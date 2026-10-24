from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
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

        try:
            with urlopen(request, timeout=45) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            provider_message = self._http_error_message(exc)
            if exc.code == 401:
                guidance = "The API key is invalid or expired. Create a new Groq API key and update GROQ_API_KEY in .env."
            elif exc.code == 403:
                guidance = (
                    "Groq denied access. Check that the key belongs to an account with API access, and "
                    f"that the account can use model '{self.model}'."
                )
            elif exc.code == 404:
                guidance = f"Model '{self.model}' was not found. Set GROQ_MODEL to an active model ID."
            elif exc.code == 429:
                guidance = "The Groq API rate or usage limit was reached. Check account limits and retry later."
            else:
                guidance = "Check Groq API access, model configuration, and account limits."
            detail = f" Provider detail: {provider_message}" if provider_message else ""
            raise RuntimeError(f"Groq API returned HTTP {exc.code}. {guidance}{detail}") from exc
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
            "You are the reasoning agent in a production incident investigation. Use only the incident "
            "report and supplied evidence; do not invent evidence or rely on external answer keys. "
            "Return JSON with ranked_hypotheses (array of objects containing cause, confidence from 0 to 1, "
            "support array, contradictions array). Every support or contradiction entry must be copied "
            "verbatim from the supplied evidence. Facts must also be verbatim evidence. Also return assumptions (array), "
            "recommended_actions (array), inconclusive (boolean), and evidence_summary (string). "
            "If evidence is weak or conflicting, return an inconclusive result and lower confidence.",
            {"incident_report": incident_report, "evidence": evidence},
        )
        if not isinstance(result.get("ranked_hypotheses"), list):
            raise RuntimeError("Groq reasoning response must include ranked_hypotheses.")
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
