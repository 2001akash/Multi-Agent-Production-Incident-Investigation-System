from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def prevent_external_llm_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("ORANGE_LLM_DISABLED", "1")
