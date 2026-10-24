from __future__ import annotations

import json
import os
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .agents import Orchestrator
from .dataset import INCIDENTS


_INCIDENTS_BY_ID = {incident.incident_id: incident for incident in INCIDENTS}
_MAX_REQUEST_BYTES = 4096

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Incident Investigator</title>
  <style>
    :root { color-scheme: light; font-family: Inter, "Segoe UI", sans-serif; color: #172033; background: #f3f6fb; }
    * { box-sizing: border-box; }
    body { margin: 0; }
    header { background: #172b4d; color: white; padding: 24px max(24px, calc((100vw - 1050px) / 2)); }
    header h1 { margin: 0 0 6px; font-size: 1.5rem; }
    header p { margin: 0; color: #d4deee; }
    main { max-width: 1050px; margin: 28px auto; padding: 0 20px 40px; }
    .panel, .card { background: white; border: 1px solid #e0e6ef; border-radius: 12px; padding: 20px; box-shadow: 0 3px 12px #172b4d0b; }
    .controls { display: grid; grid-template-columns: 1fr auto; gap: 14px; align-items: end; }
    label { display: block; font-size: .88rem; font-weight: 650; margin-bottom: 8px; }
    select, button { border-radius: 8px; min-height: 42px; font: inherit; }
    select { width: 100%; border: 1px solid #bdc8d8; padding: 8px 12px; background: white; }
    button { border: 0; padding: 0 18px; background: #1769aa; color: white; font-weight: 650; cursor: pointer; }
    button:hover { background: #10598f; } button:disabled { opacity: .6; cursor: wait; }
    #status { margin: 14px 0 0; color: #546176; min-height: 1.3em; }
    #report { margin-top: 22px; display: grid; gap: 16px; }
    .hidden { display: none !important; }
    .report-head { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
    .badge { display: inline-block; border-radius: 20px; padding: 5px 10px; background: #e8f4ff; color: #145589; font-weight: 650; font-size: .85rem; }
    .badge.warning { background: #fff1d6; color: #805500; }
    h2 { font-size: 1.1rem; margin: 0 0 12px; } h3 { margin: 0; font-size: 1rem; }
    .hypothesis { border-top: 1px solid #e8edf4; padding: 14px 0 0; margin-top: 14px; }
    .hypothesis:first-child { border-top: 0; padding-top: 0; margin-top: 0; }
    .row { display: flex; justify-content: space-between; gap: 12px; }
    .muted { color: #68758a; font-size: .9rem; }
    ul { padding-left: 20px; margin: 8px 0 0; } li { margin: 6px 0; line-height: 1.45; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    @media (max-width: 650px) { .controls, .grid { grid-template-columns: 1fr; } button { width: 100%; } }
  </style>
</head>
<body>
  <header><h1>Production Incident Investigator</h1><p>Explore synthetic incident evidence and review ranked root-cause hypotheses.</p></header>
  <main>
    <section class="panel">
      <div class="controls">
        <div><label for="incident">Synthetic incident</label><select id="incident"><option value="">Loading incidents…</option></select></div>
        <button id="investigate">Investigate</button>
      </div>
      <p id="status" role="status" aria-live="polite"></p>
    </section>
    <section id="report" class="hidden" aria-live="polite"></section>
  </main>
  <script>
    const selector = document.querySelector("#incident");
    const button = document.querySelector("#investigate");
    const status = document.querySelector("#status");
    const reportRoot = document.querySelector("#report");
    const make = (tag, text, className) => {
      const node = document.createElement(tag);
      if (text !== undefined) node.textContent = text;
      if (className) node.className = className;
      return node;
    };
    const list = (items) => {
      const ul = make("ul");
      for (const item of items || []) ul.append(make("li", item));
      return ul;
    };
    function render(data) {
      reportRoot.replaceChildren();
      const top = make("section", undefined, "card");
      const titleRow = make("div", undefined, "report-head");
      titleRow.append(make("h2", `${data.incident_id} investigation`));
      const badge = make("span", data.inconclusive ? "Inconclusive" : `Confidence ${(data.confidence * 100).toFixed(0)}%`, data.inconclusive ? "badge warning" : "badge");
      titleRow.append(badge);
      top.append(titleRow, make("p", data.evidence_summary, "muted"));
      const provider = data.llm_provider === "Groq"
        ? `Provider: Groq${data.llm_model ? ` · ${data.llm_model}` : ""}`
        : `Provider: ${data.llm_provider} (offline fallback; not LLM-generated)`;
      top.append(make("p", provider, "muted"));
      const hypothesisList = make("div");
      for (const h of data.ranked_hypotheses || []) {
        const item = make("article", undefined, "hypothesis");
        const row = make("div", undefined, "row");
        row.append(make("strong", h.cause), make("span", `${(h.confidence * 100).toFixed(0)}%`));
        item.append(row, make("p", `${h.evidence_count} directly matching evidence item(s)`, "muted"));
        if (h.support.length) { item.append(make("strong", "Supporting")); item.append(list(h.support)); }
        if (h.contradictions.length) { item.append(make("strong", "Potential contradictions")); item.append(list(h.contradictions)); }
        hypothesisList.append(item);
      }
      top.append(hypothesisList);
      const grid = make("div", undefined, "grid");
      const facts = make("section", undefined, "card");
      facts.append(make("h2", "Evidence and facts"), list(data.facts));
      const actions = make("section", undefined, "card");
      actions.append(make("h2", "Recommended actions"), list(data.recommended_actions));
      const assumptions = make("section", undefined, "card");
      assumptions.append(make("h2", "Assumptions"), list(data.assumptions));
      grid.append(facts, actions, assumptions);
      reportRoot.append(top, grid);
      reportRoot.classList.remove("hidden");
    }
    async function loadIncidents() {
      try {
        const response = await fetch("/api/incidents");
        if (!response.ok) throw new Error(`Could not load incidents (${response.status})`);
        const incidents = await response.json();
        selector.replaceChildren();
        for (const incident of incidents) {
          const option = document.createElement("option");
          option.value = incident.incident_id;
          option.textContent = `${incident.incident_id} — ${incident.title}`;
          selector.append(option);
        }
        status.textContent = "Select a scenario to run its investigation.";
      } catch (error) { status.textContent = error.message; }
    }
    button.addEventListener("click", async () => {
      button.disabled = true;
      reportRoot.classList.add("hidden");
      status.textContent = "Investigating…";
      try {
        const response = await fetch("/api/investigate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ incident_id: selector.value })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `Investigation failed (${response.status})`);
        render(data);
        status.textContent = "Investigation complete.";
      } catch (error) { status.textContent = error.message; }
      finally { button.disabled = false; }
    });
    loadIncidents();
  </script>
</body>
</html>
"""


def _serialize_report(incident_id: str) -> dict[str, Any]:
    incident = _INCIDENTS_BY_ID.get(incident_id)
    if incident is None:
        raise KeyError(incident_id)
    orchestrator = Orchestrator(incident)
    offline_explicitly_enabled = os.getenv("ORANGE_LLM_DISABLED", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if not orchestrator.llm.enabled and not offline_explicitly_enabled:
        raise RuntimeError(
            "Groq is not enabled. Configure GROQ_API_KEY, or explicitly use --offline "
            "to run the labeled deterministic fallback."
        )
    return asdict(orchestrator.investigate())


class IncidentRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            body = _INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/incidents":
            self._send_json(200, [
                {
                    "incident_id": incident.incident_id,
                    "title": incident.title,
                    "report": incident.report,
                }
                for incident in INCIDENTS
            ])
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/investigate":
            self._send_json(404, {"error": "Not found"})
            return

        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            self._send_json(411, {"error": "Content-Length is required"})
            return
        try:
            content_length = int(raw_length)
        except ValueError:
            self._send_json(400, {"error": "Invalid Content-Length"})
            return
        if content_length < 0 or content_length > _MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Request body is too large"})
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(400, {"error": "Request body must be valid JSON"})
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("incident_id"), str):
            self._send_json(400, {"error": "Provide an incident_id string"})
            return
        try:
            self._send_json(200, _serialize_report(payload["incident_id"]))
        except KeyError:
            self._send_json(404, {"error": "Unknown incident_id"})
        except RuntimeError as exc:
            self._send_json(502, {"error": str(exc)})


def run_web_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), IncidentRequestHandler)
    print(f"Incident Investigator UI available at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down web server.")
    finally:
        server.server_close()
