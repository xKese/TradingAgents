"""FastAPI server for the local TradingAgents web UI.

Serves a single-page front-end and streams a full analysis run to the browser
over Server-Sent Events (SSE). The run itself mirrors the CLI's streaming loop
(``graph.graph.stream(..., stream_mode="values")``) and reuses the shared
report writer, so a browser run is equivalent to a terminal run.

Bind to localhost only by default — this is a single-user local tool with no
auth layer.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from cli.stats_handler import StatsCallbackHandler
from tradingagents.dataflows.alpha_vantage import get_symbol_search
from tradingagents.dataflows.utils import safe_ticker_component
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.reporting import write_report_tree
from tradingagents.runtime import running_in_docker
from webapp import catalog
from webapp.run_config import RunRequestError, build_run

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="TradingAgents — Local Web UI", docs_url=None, redoc_url=None)


# --------------------------------------------------------------------------- #
# Static + catalog endpoints
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/assets/{name}")
def asset(name: str) -> FileResponse:
    # Serve only files that live directly in static/ (no path traversal).
    target = (STATIC_DIR / name).resolve()
    if target.parent != STATIC_DIR.resolve() or not target.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(target)


@app.get("/api/catalog")
def api_catalog(asset_type: str = "stock") -> JSONResponse:
    return JSONResponse(catalog.catalog(asset_type))


@app.get("/api/models")
def api_models(provider: str) -> JSONResponse:
    return JSONResponse(catalog.models(provider))


@app.get("/api/asset-type")
def api_asset_type(ticker: str) -> JSONResponse:
    return JSONResponse({"asset_type": catalog.asset_type_for(ticker)})


@app.get("/api/symbol-search")
def api_symbol_search(q: str = "") -> JSONResponse:
    # Ticker autocomplete via Alpha Vantage SYMBOL_SEARCH. This is a soft,
    # additive convenience: any failure (no ALPHA_VANTAGE_API_KEY, rate limit,
    # network blip) degrades to no suggestions — the ticker field still accepts
    # free text and the run pipeline is unaffected.
    term = q.strip()
    if len(term) < 2:
        return JSONResponse({"results": []})
    try:
        results = get_symbol_search(term)
    except Exception:
        results = []
    return JSONResponse({"results": results})


# --------------------------------------------------------------------------- #
# Progress tracking (decoupled from the CLI's rich MessageBuffer)
# --------------------------------------------------------------------------- #
_ANALYST_AGENTS = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
_ANALYST_ORDER = ["market", "social", "news", "fundamentals"]
_ANALYST_REPORT = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}

# Section wire-name -> human title (for report events).
_REPORT_TITLES = {
    "market_report": "Market Analyst",
    "sentiment_report": "Sentiment Analyst",
    "news_report": "News Analyst",
    "fundamentals_report": "Fundamentals Analyst",
    "bull_history": "Bull Researcher",
    "bear_history": "Bear Researcher",
    "investment_plan": "Research Manager",
    "trader_investment_plan": "Trader",
    "risk_analysis": "Risk Debate",
    "final_trade_decision": "Portfolio Manager",
}


class ProgressTracker:
    """Derive agent statuses and report deltas from full-state stream chunks."""

    def __init__(self, selected_analysts: list[str]):
        self.selected = [a.lower() for a in selected_analysts]
        self.status: dict[str, str] = {}
        for key in self.selected:
            self.status[_ANALYST_AGENTS[key]] = "pending"
        for name in (
            "Bull Researcher",
            "Bear Researcher",
            "Research Manager",
            "Trader",
            "Aggressive Analyst",
            "Neutral Analyst",
            "Conservative Analyst",
            "Portfolio Manager",
        ):
            self.status[name] = "pending"
        self._reports: dict[str, str] = {}

    def initial_agents(self) -> list[dict]:
        teams = [
            ("Analyst Team", [_ANALYST_AGENTS[k] for k in self.selected]),
            ("Research Team", ["Bull Researcher", "Bear Researcher", "Research Manager"]),
            ("Trading Team", ["Trader"]),
            ("Risk Management", ["Aggressive Analyst", "Neutral Analyst", "Conservative Analyst"]),
            ("Portfolio Management", ["Portfolio Manager"]),
        ]
        out = []
        for team, agents in teams:
            for name in agents:
                out.append({"team": team, "name": name, "status": self.status.get(name, "pending")})
        return out

    def _set(self, name: str, status: str, events: list):
        if self.status.get(name) != status:
            self.status[name] = status
            events.append(("status", {"agent": name, "status": status}))

    def _report(self, section: str, content, events: list):
        if not content:
            return
        content = content if isinstance(content, str) else str(content)
        if self._reports.get(section) == content:
            return
        self._reports[section] = content
        events.append(
            ("report", {"section": section, "title": _REPORT_TITLES.get(section, section), "content": content})
        )

    def process(self, chunk: dict) -> list:
        """Return a list of (event_name, payload) derived from one chunk."""
        events: list = []

        # --- Analysts ---
        found_active = False
        for key in _ANALYST_ORDER:
            if key not in self.selected:
                continue
            name = _ANALYST_AGENTS[key]
            report_key = _ANALYST_REPORT[key]
            if chunk.get(report_key):
                self._report(report_key, chunk[report_key], events)
            has_report = bool(self._reports.get(report_key))
            if has_report:
                self._set(name, "completed", events)
            elif not found_active:
                self._set(name, "in_progress", events)
                found_active = True
            else:
                self._set(name, "pending", events)

        analysts_done = not found_active and self.selected

        # --- Research debate ---
        debate = chunk.get("investment_debate_state") or {}
        if analysts_done and self.status.get("Bull Researcher") == "pending":
            self._set("Bull Researcher", "in_progress", events)
        if debate.get("bull_history"):
            self._report("bull_history", debate["bull_history"], events)
            self._set("Bull Researcher", "completed", events)
            if self.status.get("Bear Researcher") == "pending":
                self._set("Bear Researcher", "in_progress", events)
        if debate.get("bear_history"):
            self._report("bear_history", debate["bear_history"], events)
            self._set("Bear Researcher", "completed", events)
            if self.status.get("Research Manager") == "pending":
                self._set("Research Manager", "in_progress", events)
        if chunk.get("investment_plan") or debate.get("judge_decision"):
            self._report("investment_plan", chunk.get("investment_plan") or debate.get("judge_decision"), events)
            self._set("Research Manager", "completed", events)
            if self.status.get("Trader") == "pending":
                self._set("Trader", "in_progress", events)

        # --- Trader ---
        if chunk.get("trader_investment_plan"):
            self._report("trader_investment_plan", chunk["trader_investment_plan"], events)
            self._set("Trader", "completed", events)
            if self.status.get("Aggressive Analyst") == "pending":
                self._set("Aggressive Analyst", "in_progress", events)

        # --- Risk management ---
        risk = chunk.get("risk_debate_state") or {}
        if risk.get("aggressive_history"):
            self._set("Aggressive Analyst", "completed", events)
            if self.status.get("Neutral Analyst") == "pending":
                self._set("Neutral Analyst", "in_progress", events)
        if risk.get("neutral_history"):
            self._set("Neutral Analyst", "completed", events)
            if self.status.get("Conservative Analyst") == "pending":
                self._set("Conservative Analyst", "in_progress", events)
        if risk.get("conservative_history"):
            self._set("Conservative Analyst", "completed", events)
            if self.status.get("Portfolio Manager") == "pending":
                self._set("Portfolio Manager", "in_progress", events)
        risk_text = "\n\n".join(
            part for part in (
                risk.get("aggressive_history"),
                risk.get("conservative_history"),
                risk.get("neutral_history"),
            ) if part
        )
        if risk_text:
            self._report("risk_analysis", risk_text, events)
        if chunk.get("final_trade_decision"):
            self._report("final_trade_decision", chunk["final_trade_decision"], events)
            self._set("Portfolio Manager", "completed", events)

        return events


# --------------------------------------------------------------------------- #
# Message extraction (compact, self-contained)
# --------------------------------------------------------------------------- #
def _extract_content(content) -> str | None:
    if content is None or content == "":
        return None
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, dict):
        text = content.get("text", "")
        return text.strip() if isinstance(text, str) and text.strip() else None
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")).strip())
            elif isinstance(item, str):
                parts.append(item.strip())
        joined = " ".join(p for p in parts if p)
        return joined or None
    return str(content).strip() or None


def _classify(message) -> tuple[str, str | None]:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    content = _extract_content(getattr(message, "content", None))
    if isinstance(message, HumanMessage):
        return ("Control" if content == "Continue" else "User", content)
    if isinstance(message, ToolMessage):
        return ("Data", content)
    if isinstance(message, AIMessage):
        return ("Agent", content)
    return ("Agent", content)


# --------------------------------------------------------------------------- #
# Run streaming
# --------------------------------------------------------------------------- #
def _preflight_key_error(provider: str) -> str | None:
    """Return a helpful message if the provider needs a key that isn't set.

    Local/keyless providers (Ollama, OpenAI-compatible servers like LM Studio
    or vLLM) are exempt — they authenticate optionally or not at all.
    """
    if not catalog.requires_api_key(provider):
        return None
    key_env = get_api_key_env(provider)
    if key_env and not os.environ.get(key_env):
        return (
            f"Für den Provider '{provider}' ist kein API-Key gesetzt. "
            f"Bitte die Umgebungsvariable {key_env} setzen (oder einen lokalen "
            f"Provider wie Ollama oder LM Studio wählen)."
        )
    return None


def _is_loopback_url(url: str | None) -> bool:
    """Whether ``url``'s host is a loopback address (localhost / 127.0.0.1)."""
    if not url:
        return False
    to_parse = url if "://" in url else "http://" + url
    host = (urlparse(to_parse).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _friendly_error_message(exc: Exception, spec: dict) -> str:
    """Turn an SDK exception into an actionable message for the UI.

    A bare ``APIConnectionError: Connection error.`` tells the user nothing;
    when the LLM endpoint is unreachable, name the backend URL that failed and
    the two usual causes (server not running / Docker loopback), in German to
    match the rest of the UI.
    """
    raw = f"{type(exc).__name__}: {exc}"
    if type(exc).__name__ != "APIConnectionError":
        return raw

    backend_url = spec.get("config", {}).get("backend_url") or "(Standard-Endpunkt)"
    lines = [
        f"Verbindung zum LLM-Endpunkt fehlgeschlagen: {backend_url}",
        "Läuft der Modell-Server (z. B. LM Studio, Ollama, vLLM) und ist ein "
        "Modell geladen?",
    ]
    if _is_loopback_url(backend_url) and running_in_docker():
        lines.append(
            "Hinweis: Die App läuft in Docker. 'localhost' zeigt dort auf den "
            "Container, nicht auf den Host. Verwende "
            "'http://host.docker.internal:<Port>/v1' als Backend-URL, damit der "
            "Server auf dem Host erreichbar ist."
        )
    lines.append(f"Details: {raw}")
    return "\n".join(lines)


def _run_worker(spec: dict, q: queue.Queue):
    """Execute the graph in a worker thread, pushing SSE events onto ``q``."""

    def emit(event: str, payload: dict):
        q.put((event, payload))

    try:
        provider = spec["config"]["llm_provider"]
        key_msg = _preflight_key_error(provider)
        if key_msg:
            emit("error", {"message": key_msg})
            return

        stats = StatsCallbackHandler()
        tracker = ProgressTracker(spec["analysts"])

        emit(
            "run",
            {
                "ticker": spec["ticker"],
                "analysis_date": spec["analysis_date"],
                "asset_type": spec["asset_type"],
                "provider": provider,
                "agents": tracker.initial_agents(),
            },
        )

        graph = TradingAgentsGraph(
            selected_analysts=spec["analysts"],
            debug=False,
            config=spec["config"],
            callbacks=[stats],
        )

        instrument_context = graph.resolve_instrument_context(
            spec["ticker"], spec["asset_type"]
        )
        init_state = graph.propagator.create_initial_state(
            spec["ticker"],
            spec["analysis_date"],
            asset_type=spec["asset_type"],
            instrument_context=instrument_context,
        )
        args = graph.propagator.get_graph_args(callbacks=[stats])

        seen_msg_ids: set = set()
        trace = []
        for chunk in graph.graph.stream(init_state, **args):
            # Messages + tool calls
            for message in chunk.get("messages", []):
                mid = getattr(message, "id", None)
                if mid is not None and mid in seen_msg_ids:
                    continue
                if mid is not None:
                    seen_msg_ids.add(mid)
                mtype, content = _classify(message)
                if content and content.strip():
                    emit("message", {"mtype": mtype, "content": content})
                for tc in getattr(message, "tool_calls", None) or []:
                    emit("tool", {"name": tc.get("name", ""), "args": tc.get("args", {})})

            for event, payload in tracker.process(chunk):
                emit(event, payload)

            emit("stats", stats.get_stats())
            trace.append(chunk)

        # Merge chunks into the final state (same as the CLI).
        final_state: dict = {}
        for chunk in trace:
            final_state.update(chunk)

        decision = graph.process_signal(final_state["final_trade_decision"])

        # Write the shared report tree to disk.
        report_path = None
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            save_path = (
                Path(spec["config"]["results_dir"])
                / "reports"
                / f"{safe_ticker_component(spec['ticker'])}_{stamp}"
            )
            report_path = str(write_report_tree(final_state, spec["ticker"], save_path))
        except Exception:  # noqa: BLE001 — report writing must not fail the run
            report_path = None

        emit(
            "final",
            {
                "decision": decision,
                "report_path": report_path,
                "reports": {
                    "market_report": final_state.get("market_report"),
                    "sentiment_report": final_state.get("sentiment_report"),
                    "news_report": final_state.get("news_report"),
                    "fundamentals_report": final_state.get("fundamentals_report"),
                    "investment_plan": final_state.get("investment_plan"),
                    "trader_investment_plan": final_state.get("trader_investment_plan"),
                    "final_trade_decision": final_state.get("final_trade_decision"),
                },
                "stats": stats.get_stats(),
            },
        )
    except Exception as exc:  # noqa: BLE001 — surface any run failure to the UI
        message = _friendly_error_message(exc, spec)
        emit(
            "error",
            {"message": message, "trace": traceback.format_exc()},
        )
    finally:
        q.put(("done", {}))


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.post("/api/run")
async def api_run(request: Request) -> StreamingResponse:
    body = await request.json()
    try:
        spec = build_run(body)
    except RunRequestError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    q: queue.Queue = queue.Queue()
    worker = threading.Thread(target=_run_worker, args=(spec, q), daemon=True)
    worker.start()

    async def event_stream():
        import asyncio

        def _next():
            """Blocking queue read with a short timeout, run off the event loop."""
            try:
                return q.get(timeout=0.25)
            except queue.Empty:
                return None

        yield _sse("open", {"ticker": spec["ticker"]})
        while True:
            if await request.is_disconnected():
                break
            item = await asyncio.to_thread(_next)
            if item is None:
                continue
            event, payload = item
            if event == "done":
                yield _sse("done", {})
                break
            yield _sse(event, payload)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def create_app() -> FastAPI:
    """Return the FastAPI app (hook for programmatic/uvicorn factory use)."""
    return app
