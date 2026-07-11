"""Local-first web cockpit for cached personal research artifacts.

The cockpit intentionally reads only the JSONL cache written by the research
platform.  It does not call market-data vendors or invoke the legacy agent
graph, which keeps the viewing surface deterministic and cheap to run.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from tradingagents.dataflows.utils import safe_ticker_component

from .artifact_store import JsonArtifactStore
from .company_profile import build_company_profile
from .data_health import build_cache_data_health
from .financial_health import assess_financial_health
from .report_workspace import build_report_workspace, render_archived_report
from .research_jobs import LocalResearchJobRunner, ResearchJobRequest
from .research_readiness import build_research_readiness
from .run_archive import JsonResearchRunArchive
from .valuation_context import build_valuation_context
from .watchlist import JsonWatchlistStore
from .watchlist_board import build_watchlist_board
from .watchlist_refresh import WatchlistRefreshRequest, submit_watchlist_refresh

_EARLIEST_DATE = date(1900, 1, 1)


def discover_cached_symbols(
    store: JsonArtifactStore,
    watchlist: JsonWatchlistStore | None = None,
) -> list[str]:
    """Return symbols which have at least one cached artifact file."""

    symbols: set[str] = set()
    for kind in ("prices", "fundamentals", "news", "agent_outputs"):
        directory = store.root / kind
        if directory.exists():
            symbols.update(path.stem for path in directory.glob("*.jsonl"))
    runs_directory = store.root / "runs"
    if runs_directory.exists():
        symbols.update(path.name for path in runs_directory.iterdir() if path.is_dir())
    if watchlist is not None:
        symbols.update(entry.symbol for entry in watchlist.list_entries())
    return sorted(symbols)


def build_cockpit_snapshot(
    store: JsonArtifactStore,
    symbol: str,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build one JSON-ready, read-only cockpit view from local artifacts."""

    normalized_symbol = symbol.strip().upper()
    if not normalized_symbol:
        raise ValueError("symbol is required")

    bars = sorted(
        store.load_price_bars(normalized_symbol, _EARLIEST_DATE, date.max),
        key=lambda bar: bar.date,
    )
    fundamentals = sorted(
        store.load_fundamentals(normalized_symbol),
        key=lambda item: (item.provenance.as_of_date, item.period_end),
    )
    news = sorted(
        store.load_news(normalized_symbol, _EARLIEST_DATE, date.max),
        key=lambda item: item.published_at,
        reverse=True,
    )
    agent_outputs = sorted(
        store.load_agent_outputs(normalized_symbol),
        key=lambda item: (item.as_of_date, item.agent_id, item.output_type.value),
        reverse=True,
    )

    market = _market_summary(bars)
    latest_fundamentals = fundamentals[-1] if fundamentals else None
    financial_quality = [
        item for item in fundamentals if (item.fiscal_period or "").startswith("financial_report_")
    ]
    latest_financial_quality = financial_quality[-1] if financial_quality else None
    financial_quality_history = [
        item
        for _, item in sorted(
            {item.period_end: item for item in financial_quality}.items(),
            reverse=True,
        )[:8]
    ]
    company_profile = build_company_profile(fundamentals, symbol=normalized_symbol)
    valuation_context = build_valuation_context(fundamentals)
    financial_health = assess_financial_health(latest_financial_quality)
    archive = JsonResearchRunArchive(store.root)
    runs = archive.list_runs(normalized_symbol)
    selected_run_id = run_id or (runs[0].run_id if runs else None)
    latest_run = (
        archive.load_bundle(normalized_symbol, selected_run_id)
        if selected_run_id is not None
        else None
    )
    if selected_run_id is not None and latest_run is None:
        raise ValueError("run_id was not found for this symbol")
    data_health = build_cache_data_health(
        price_bars=bars,
        fundamentals=fundamentals,
        news=news,
        reference_as_of_date=(latest_run.as_of_date.date() if latest_run is not None else None),
    )
    readiness = build_research_readiness(
        data_health=data_health,
        valuation_context=valuation_context,
        financial_health=financial_health,
        agent_outputs=(latest_run.agent_outputs if latest_run is not None else agent_outputs),
        signal=(latest_run.signal if latest_run is not None else None),
        risk_review=(latest_run.risk_review if latest_run is not None else None),
        backtest_result=(latest_run.backtest_result if latest_run is not None else None),
    )
    return {
        "symbol": normalized_symbol,
        "has_data": bool(bars or fundamentals or news or agent_outputs or latest_run),
        "market": market,
        "fundamentals": (
            latest_fundamentals.model_dump(mode="json") if latest_fundamentals is not None else None
        ),
        "financial_quality": (
            latest_financial_quality.model_dump(mode="json")
            if latest_financial_quality is not None
            else None
        ),
        "company_profile": company_profile.model_dump(mode="json"),
        "valuation_context": valuation_context.model_dump(mode="json"),
        "research_readiness": readiness.model_dump(mode="json"),
        "financial_health": financial_health.model_dump(mode="json"),
        "financial_quality_history": [
            item.model_dump(mode="json") for item in financial_quality_history
        ],
        "news": [item.model_dump(mode="json") for item in news[:12]],
        "agent_outputs": [item.model_dump(mode="json") for item in agent_outputs[:12]],
        "latest_run": _run_summary(latest_run, run_id=selected_run_id),
        "report_workspace": build_report_workspace(latest_run),
        "data_health": data_health,
        "runs": [item.model_dump(mode="json") for item in runs[:20]],
        "artifact_counts": {
            "price_bars": len(bars),
            "fundamental_snapshots": len(fundamentals),
            "news_items": len(news),
            "agent_outputs": len(agent_outputs),
        },
    }


def _market_summary(bars: list[Any]) -> dict[str, Any] | None:
    if not bars:
        return None
    first, last = bars[0], bars[-1]
    period_return = last.close / first.close - 1.0 if first.close else None
    return {
        "first_date": first.date.isoformat(),
        "last_date": last.date.isoformat(),
        "last_close": last.close,
        "currency": last.currency,
        "period_return_pct": period_return,
        "latest_volume": last.volume,
        "bar_count": len(bars),
        "series": [
            {"date": bar.date.isoformat(), "close": bar.close, "volume": bar.volume}
            for bar in bars[-90:]
        ],
    }


def _run_summary(
    bundle: Any | None,
    *,
    run_id: str | None,
) -> dict[str, Any] | None:
    if bundle is None:
        return None

    backtest = bundle.backtest_result
    return {
        "run_id": run_id,
        "as_of_date": bundle.as_of_date.isoformat(),
        "generated_at": bundle.generated_at.isoformat(),
        "signal": bundle.signal.model_dump(mode="json") if bundle.signal is not None else None,
        "risk_review": (
            bundle.risk_review.model_dump(mode="json") if bundle.risk_review is not None else None
        ),
        "backtest": (
            {
                "metrics": backtest.metrics.model_dump(mode="json"),
                "trade_count": len(backtest.trades),
                "round_trip_count": len(backtest.round_trips),
                "warning_count": len(backtest.warning_events) or len(backtest.warnings),
            }
            if backtest is not None
            else None
        ),
    }


class CockpitRequestHandler(BaseHTTPRequestHandler):
    """Serve the local cockpit and its read-only JSON endpoints."""

    store: JsonArtifactStore
    watchlist: JsonWatchlistStore
    jobs: LocalResearchJobRunner

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(HTTPStatus.OK, _APP_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed.path == "/api/research-jobs":
            self._send_json(
                HTTPStatus.OK,
                {"jobs": [job.model_dump(mode="json") for job in self.jobs.list_jobs()]},
            )
            return
        if parsed.path.startswith("/api/research-jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = self.jobs.get(job_id)
            if job is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "job not found"})
            else:
                self._send_json(HTTPStatus.OK, {"job": job.model_dump(mode="json")})
            return
        if parsed.path.startswith("/api/reports/"):
            self._serve_archived_report(parsed)
            return
        if parsed.path == "/api/symbols":
            self._send_json(
                HTTPStatus.OK,
                {"symbols": discover_cached_symbols(self.store, self.watchlist)},
            )
            return
        if parsed.path == "/api/watchlist-board":
            self._send_json(HTTPStatus.OK, build_watchlist_board(self.store, self.watchlist))
            return
        if parsed.path == "/api/watchlist":
            self._send_json(
                HTTPStatus.OK,
                {
                    "entries": [
                        entry.model_dump(mode="json") for entry in self.watchlist.list_entries()
                    ]
                },
            )
            return
        if parsed.path == "/api/snapshot":
            query = parse_qs(parsed.query)
            symbol = query.get("symbol", [""])[0]
            run_id = query.get("run_id", [None])[0]
            try:
                self._send_json(
                    HTTPStatus.OK,
                    build_cockpit_snapshot(self.store, symbol, run_id=run_id),
                )
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _serve_archived_report(self, parsed) -> None:
        parts = parsed.path.split("/")
        if len(parts) != 5 or not parts[-1].endswith(".md"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "report not found"})
            return
        symbol = parts[3]
        run_id = parts[4][:-3]
        bundle = JsonResearchRunArchive(self.store.root).load_bundle(symbol, run_id)
        try:
            report = render_archived_report(bundle)
        except ValueError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": str(error)})
            return
        download = parse_qs(parsed.query).get("download", ["0"])[0] == "1"
        disposition = None
        if download:
            disposition = (
                f'attachment; filename="{safe_ticker_component(bundle.symbol)}_{run_id}.md"'
            )
        self._send_text(
            HTTPStatus.OK,
            report,
            "text/markdown; charset=utf-8",
            content_disposition=disposition,
        )

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if urlparse(self.path).path == "/api/research-jobs":
            try:
                request = ResearchJobRequest.model_validate(self._read_json_body())
            except (UnicodeDecodeError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            job = self.jobs.submit(request)
            self._send_json(HTTPStatus.ACCEPTED, {"job": job.model_dump(mode="json")})
            return
        if urlparse(self.path).path == "/api/watchlist-refresh":
            try:
                request = WatchlistRefreshRequest.model_validate(self._read_json_body())
            except (UnicodeDecodeError, ValueError) as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            batch = submit_watchlist_refresh(self.watchlist, self.jobs, request)
            self._send_json(HTTPStatus.ACCEPTED, batch.model_dump(mode="json"))
            return
        if urlparse(self.path).path != "/api/watchlist":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            payload = self._read_json_body()
            entry = self.watchlist.add(str(payload.get("symbol", "")))
        except (UnicodeDecodeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.CREATED, {"entry": entry.model_dump(mode="json")})

    def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path != "/api/watchlist":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        symbol = parse_qs(parsed.query).get("symbol", [""])[0]
        try:
            removed = self.watchlist.remove(symbol)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.OK, {"removed": removed})

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("expected a JSON request body") from error
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep normal browser navigation out of the terminal."""

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send_text(
            status, json.dumps(payload, ensure_ascii=True), "application/json; charset=utf-8"
        )

    def _send_text(
        self,
        status: HTTPStatus,
        payload: str,
        content_type: str,
        *,
        content_disposition: str | None = None,
    ) -> None:
        encoded = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        if content_disposition is not None:
            self.send_header("Content-Disposition", content_disposition)
        self.end_headers()
        self.wfile.write(encoded)


def create_cockpit_server(
    data_dir: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> ThreadingHTTPServer:
    """Create a local server without starting it, suitable for tests and CLI use."""

    store = JsonArtifactStore(data_dir)
    watchlist = JsonWatchlistStore(data_dir)
    jobs = LocalResearchJobRunner(data_dir)

    class BoundCockpitRequestHandler(CockpitRequestHandler):
        pass

    BoundCockpitRequestHandler.store = store
    BoundCockpitRequestHandler.watchlist = watchlist
    BoundCockpitRequestHandler.jobs = jobs
    return ThreadingHTTPServer((host, port), BoundCockpitRequestHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local personal research cockpit.")
    parser.add_argument(
        "--data-dir",
        default=".research-data",
        help="JSONL artifact cache directory (default: .research-data)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    args = parser.parse_args(argv)

    server = create_cockpit_server(args.data_dir, host=args.host, port=args.port)
    print(f"Research cockpit ready at http://{args.host}:{args.port}")
    print(f"Reading local artifacts from {Path(args.data_dir).resolve()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nResearch cockpit stopped.")
    finally:
        server.server_close()
    return 0


_APP_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Research Cockpit</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #15212b; background: #f4f7f8; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #f4f7f8; }
    button, input, select { font: inherit; }
    .shell { max-width: 1440px; margin: 0 auto; padding: 28px 32px 44px; }
    .topbar { display: flex; justify-content: space-between; gap: 20px; align-items: end; border-bottom: 1px solid #d5dfe3; padding-bottom: 20px; }
    h1 { margin: 0; font-size: 25px; line-height: 1.2; font-weight: 700; letter-spacing: 0; }
    .eyebrow { margin: 0 0 7px; color: #39706e; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    .control { display: flex; gap: 8px; align-items: center; }
    input, select, button { height: 36px; border: 1px solid #b8c7cd; border-radius: 5px; background: #fff; color: #15212b; padding: 0 11px; }
    select { min-width: 150px; } input { width: 104px; } textarea { min-height: 74px; resize: vertical; border: 1px solid #b8c7cd; border-radius: 5px; background: #fff; color: #15212b; padding: 9px 11px; font: inherit; } .decision-form input, .decision-form select, .decision-form textarea { width: 100%; min-width: 0; } .decision-form .wide { grid-column: 1 / -1; }
    button { cursor: pointer; font-weight: 650; }
    button:hover { border-color: #39706e; background: #edf8f7; } button:disabled { cursor: not-allowed; color: #9aa8ad; background: #f4f7f8; }
    .status { min-height: 20px; color: #64747d; font-size: 13px; margin: 16px 0 12px; }
    .data-health { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin-top: 12px; border: 1px solid #d5dfe3; border-radius: 6px; background: #fff; } .health-item { min-height: 76px; padding: 13px 17px; border-right: 1px solid #d5dfe3; } .health-item:last-child { border-right: 0; } .health-title { color: #64747d; font-size: 12px; font-weight: 650; } .health-status { display: inline-block; margin-top: 6px; font-size: 12px; font-weight: 700; color: #176f6c; } .health-status.lagging, .health-status.missing { color: #a06628; } .health-detail { margin-top: 4px; color: #64747d; font-size: 11px; line-height: 1.35; }
    .watchlist-board { margin-top: 12px; } .table-wrap { overflow-x: auto; } .watchlist-table { width: 100%; border-collapse: collapse; font-size: 13px; } .watchlist-table th, .watchlist-table td { padding: 11px 17px; border-bottom: 1px solid #e4ebed; text-align: left; white-space: nowrap; } .watchlist-table th { color: #64747d; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; } .watchlist-table tr:last-child td { border-bottom: 0; } .watch-symbol { height: auto; border: 0; border-radius: 0; background: transparent; color: #176f6c; padding: 0; font-weight: 700; } .watch-symbol:hover { background: transparent; text-decoration: underline; } .board-status { font-size: 12px; font-weight: 700; color: #39706e; } .board-status.lagging, .board-status.missing { color: #a06628; }
    .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #d5dfe3; border-radius: 6px; background: #fff; }
    .metric { min-height: 102px; padding: 18px; border-right: 1px solid #d5dfe3; }
    .metric:last-child { border-right: 0; }
    .metric-label { color: #64747d; font-size: 12px; font-weight: 650; text-transform: uppercase; letter-spacing: .06em; }
    .metric-value { margin-top: 9px; font-size: 24px; font-weight: 700; overflow-wrap: anywhere; }
    .metric-detail { margin-top: 5px; color: #64747d; font-size: 12px; }
    .positive { color: #087f5b; } .negative { color: #bf3f46; } .neutral { color: #15212b; }
    .workspace { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, .85fr); gap: 18px; margin-top: 18px; }
    .panel { background: #fff; border: 1px solid #d5dfe3; border-radius: 6px; overflow: hidden; }
    .panel-title { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 12px; padding: 15px 17px; border-bottom: 1px solid #d5dfe3; }
    h2 { margin: 0; font-size: 15px; letter-spacing: 0; } .panel-meta { color: #64747d; font-size: 12px; }
    .chart { height: 248px; padding: 16px 18px 12px; } svg { display: block; width: 100%; height: 100%; overflow: visible; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; } .grid > div { padding: 14px 17px; border-bottom: 1px solid #e4ebed; }
    .grid > div:nth-child(odd) { border-right: 1px solid #e4ebed; }
    .label { display: block; color: #64747d; font-size: 12px; } .value { display: block; margin-top: 4px; font-weight: 650; overflow-wrap: anywhere; }
    .items { margin: 0; padding: 0; list-style: none; }
    .item { padding: 14px 17px; border-bottom: 1px solid #e4ebed; } .item:last-child { border-bottom: 0; }
    .item-title { font-weight: 650; line-height: 1.42; } .item-meta { color: #64747d; font-size: 12px; margin-top: 5px; }
    .item-summary { color: #45565f; line-height: 1.45; margin-top: 7px; font-size: 13px; }
    .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; } .tag { padding: 3px 6px; border: 1px solid #cbd8db; border-radius: 4px; color: #39706e; font-size: 11px; font-weight: 650; }
    .report-actions { display: flex; flex-wrap: wrap; gap: 8px; } .action-link { color: #176f6c; font-size: 12px; font-weight: 650; text-decoration: none; } .action-link:hover { text-decoration: underline; } .action-link[aria-disabled="true"] { color: #9aa8ad; pointer-events: none; }
    .coverage-summary { padding: 14px 17px; border-bottom: 1px solid #e4ebed; color: #45565f; font-size: 13px; } .coverage-list { margin: 0; padding: 0; list-style: none; } .coverage-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 6px 12px; padding: 11px 17px; border-bottom: 1px solid #e4ebed; } .coverage-label { font-size: 13px; font-weight: 650; } .coverage-detail { color: #64747d; font-size: 12px; } .coverage-status { color: #39706e; font-size: 11px; font-weight: 650; } .coverage-status.missing { color: #a06628; }
    .report-preview { max-height: 360px; overflow: auto; margin: 0; padding: 16px 17px; background: #fbfcfc; color: #35454d; font: 12px/1.55 ui-monospace, SFMono-Regular, Consolas, monospace; white-space: pre-wrap; }
    .empty { padding: 40px 18px; color: #64747d; text-align: center; } a { color: #176f6c; }
    @media (max-width: 860px) { .shell { padding: 20px 16px 32px; } .watchlist-table th, .watchlist-table td { padding: 10px 12px; } .data-health { grid-template-columns: 1fr; } .health-item { border-right: 0; border-bottom: 1px solid #d5dfe3; } .health-item:last-child { border-bottom: 0; } .topbar { align-items: stretch; flex-direction: column; } .metrics { grid-template-columns: 1fr 1fr; } .metric:nth-child(2) { border-right: 0; } .metric:nth-child(-n+2) { border-bottom: 1px solid #d5dfe3; } .workspace { grid-template-columns: 1fr; } }
    @media (max-width: 460px) { .control { width: 100%; flex-wrap: wrap; } .control #symbol { flex: 1 0 100%; width: 100%; } .control #watchSymbol { flex: 1 1 100px; min-width: 100px; } .control button { flex: 0 0 auto; } .metrics { grid-template-columns: 1fr; } .metric { border-right: 0; border-bottom: 1px solid #d5dfe3; } .metric:last-child { border-bottom: 0; } .grid { grid-template-columns: 1fr; } .grid > div:nth-child(odd) { border-right: 0; } }
  </style>
</head>
<body>
  <main class="shell">
    <div class="topbar">
      <div><p class="eyebrow">Local-first equity research</p><h1 id="title">Research Cockpit</h1></div>
      <div class="control">
        <select id="symbol" aria-label="Ticker symbol"></select>
        <input id="watchSymbol" aria-label="Add ticker to watchlist" placeholder="Ticker">
        <button id="addSymbol" type="button">Add</button>
        <button id="removeSymbol" type="button">Remove</button>
        <button id="runResearch" type="button">Run Research</button>
        <button id="refreshWatchlistResearch" type="button">Refresh Watchlist</button>
        <button id="refresh" type="button">Refresh</button>
      </div>
    </div>
    <p class="status" id="status">Loading local research cache...</p>
    <section class="metrics" aria-label="Market summary" id="metrics"></section>
    <section class="data-health" aria-label="Cached data health" id="dataHealth"></section>
    <section class="panel" aria-label="Research readiness"><div class="panel-title"><h2>Research Readiness</h2><span class="panel-meta" id="readinessMeta"></span></div><ul class="items" id="readiness"></ul></section>
    <section class="panel watchlist-board" aria-label="Watchlist board"><div class="panel-title"><h2>Watchlist</h2><span class="panel-meta" id="watchlistMeta"></span></div><div class="table-wrap"><table class="watchlist-table"><thead><tr><th>Symbol</th><th>Last Close</th><th>Price As Of</th><th>Data Health</th><th>Latest Research</th><th>Decision</th></tr></thead><tbody id="watchlistBoard"></tbody></table></div></section>
    <section class="workspace">
      <div class="panel"><div class="panel-title"><h2>Price History</h2><span class="panel-meta" id="chartMeta"></span></div><div class="chart" id="chart"></div></div>
      <div class="panel"><div class="panel-title"><h2>Company Profile</h2><span class="panel-meta" id="companyProfileMeta"></span></div><div class="grid" id="companyProfile"></div></div>
      <div class="panel"><div class="panel-title"><h2>Latest Fundamentals</h2><span class="panel-meta" id="fundamentalsMeta"></span></div><div class="grid" id="fundamentals"></div></div>
      <div class="panel"><div class="panel-title"><h2>Valuation Context</h2><span class="panel-meta" id="valuationContextMeta"></span></div><div class="table-wrap"><table class="watchlist-table"><thead><tr><th>Metric</th><th>Latest</th><th>Percentile</th><th>Low</th><th>Median</th><th>High</th><th>Days</th></tr></thead><tbody id="valuationContext"></tbody></table></div></div>
      <div class="panel"><div class="panel-title"><h2>Financial Quality</h2><span class="panel-meta" id="financialQualityMeta"></span></div><div class="grid" id="financialQuality"></div></div>
      <div class="panel"><div class="panel-title"><h2>Financial Health</h2><span class="panel-meta" id="financialHealthMeta"></span></div><ul class="items" id="financialHealth"></ul></div>
      <div class="panel"><div class="panel-title"><h2>Financial Trend</h2><span class="panel-meta" id="financialTrendMeta"></span></div><div class="table-wrap"><table class="watchlist-table"><thead><tr><th>Period</th><th>Revenue</th><th>Net Income</th><th>Operating Cash Flow</th><th>ROE</th></tr></thead><tbody id="financialTrend"></tbody></table></div></div>
      <div class="panel"><div class="panel-title"><h2>Structured Research</h2><span class="panel-meta" id="agentsMeta"></span></div><ul class="items" id="agents"></ul></div>
      <div class="panel"><div class="panel-title"><h2>News</h2><span class="panel-meta" id="newsMeta"></span></div><ul class="items" id="news"></ul></div>

      <div class="panel"><div class="panel-title"><h2>Research Runs</h2><select id="runHistory" aria-label="Archived research run"></select></div><div class="empty" id="runHistoryDetail"></div></div>
      <div class="panel"><div class="panel-title"><h2>Research Report</h2><div class="report-actions"><a id="openReport" class="action-link" target="_blank" rel="noreferrer" aria-disabled="true">Open Markdown</a><a id="exportReport" class="action-link" aria-disabled="true">Export .md</a></div></div><div id="reportCoverage" class="empty">Select an archived research run to view coverage.</div><pre id="reportPreview" class="report-preview">No archived report available.</pre></div>
      <div class="panel"><div class="panel-title"><h2>Decision Draft</h2><span class="panel-meta">Optional manual signal</span></div><div class="grid decision-form">
        <div class="wide"><label class="label" for="dataProvider">Data Provider</label><select id="dataProvider"><option value="auto" selected>Auto (Tushare for A/H)</option><option value="tushare">Tushare Pro</option><option value="yfinance">Yahoo Finance</option></select></div>
        <div class="wide"><label class="label" for="narrativeMode">Analysis Mode</label><select id="narrativeMode"><option value="deterministic" selected>Deterministic</option><option value="openai_narrative">OpenAI Narrative</option></select></div>
        <div><label class="label" for="decisionDirection">Direction</label><select id="decisionDirection"><option value="">No decision</option><option value="buy">Buy</option><option value="hold">Hold</option><option value="sell">Sell</option></select></div>
        <div><label class="label" for="decisionHorizon">Horizon</label><select id="decisionHorizon"><option value="short">Short</option><option value="medium" selected>Medium</option><option value="long">Long</option></select></div>
        <div><label class="label" for="decisionConfidence">Confidence (%)</label><input id="decisionConfidence" type="number" min="0" max="100" step="1" value="60"></div>
        <div><label class="label" for="decisionPosition">Proposed Position (%)</label><input id="decisionPosition" type="number" min="0" max="100" step="0.1" value="5"></div>
        <div class="wide"><label class="label" for="decisionRationale">Rationale</label><textarea id="decisionRationale">Manual cockpit decision.</textarea></div>
      </div></div>
      <div class="panel"><div class="panel-title"><h2>Decision and Risk</h2><span class="panel-meta" id="decisionMeta"></span></div><div class="grid" id="decision"></div></div>
      <div class="panel"><div class="panel-title"><h2>Backtest Snapshot</h2><span class="panel-meta" id="backtestMeta"></span></div><div class="grid" id="backtest"></div></div>
    </section>
  </main>
  <script>
    const $ = id => document.getElementById(id);
    const money = (v, c) => v == null ? 'N/A' : `${c || ''}${c ? ' ' : ''}${Number(v).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    const pct = v => v == null ? 'N/A' : `${(Number(v) * 100).toFixed(2)}%`;
    const text = value => value == null || value === '' ? 'N/A' : String(value);
    const escape = value => text(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;', "'":'&#39;'}[char]));
    function renderMetrics(snapshot) {
      const market = snapshot.market;
      const latestRun = snapshot.latest_run;
      const cards = market ? [
        ['Last Close', money(market.last_close, market.currency), market.last_date],
        ['Period Return', pct(market.period_return_pct), `${market.bar_count} cached bars`, market.period_return_pct >= 0 ? 'positive' : 'negative'],
        ['Latest Volume', market.latest_volume == null ? 'N/A' : Number(market.latest_volume).toLocaleString(), market.first_date],
        ['Latest Decision', latestRun?.risk_review?.decision || latestRun?.signal?.direction || 'N/A', latestRun ? `Archived ${latestRun.as_of_date.slice(0,10)}` : `${snapshot.artifact_counts.agent_outputs} structured outputs`]
      ] : [
        ['Last Close', 'N/A', 'No cached price data'], ['Period Return', 'N/A', 'No cached price data'], ['Latest Volume', 'N/A', 'No cached price data'], ['Latest Decision', latestRun?.risk_review?.decision || latestRun?.signal?.direction || 'N/A', latestRun ? `Archived ${latestRun.as_of_date.slice(0,10)}` : 'No archived decision']
      ];
      $('metrics').innerHTML = cards.map(([label, value, detail, cls]) => `<div class="metric"><div class="metric-label">${escape(label)}</div><div class="metric-value ${cls || 'neutral'}">${escape(value)}</div><div class="metric-detail">${escape(detail)}</div></div>`).join('');
    }
    function renderDataHealth(health) {
      const items = health?.items || [];
      if (!items.length) { $('dataHealth').innerHTML = '<div class="empty">No cached data health available.</div>'; return; }
      const reference = health.reference_as_of_date ? `Reference as of ${health.reference_as_of_date}` : 'Latest cached availability';
      $('dataHealth').innerHTML = items.map(item => `<div class="health-item"><div class="health-title">${escape(item.label)}</div><div class="health-status ${escape(item.status)}">${escape(item.status)}</div><div class="health-detail">${escape(item.detail)}${item.available_as_of_date ? ` - available as of ${escape(item.available_as_of_date)}` : ''}</div></div>`).join('');
      $('dataHealth').setAttribute('aria-label', `Cached data health: ${reference}`);
    }
    function renderReadiness(readiness) {
      if (!readiness) { $('readinessMeta').textContent = ''; $('readiness').innerHTML = '<li class="empty">No research readiness data available.</li>'; return; }
      $('readinessMeta').textContent = `${readiness.status} - ${readiness.required_ready}/${readiness.required_total} required`;
      $('readiness').innerHTML = readiness.items.map(item => `<li class="item"><div class="item-title">${escape(item.label)}</div><div class="item-meta">${escape(item.status)} | ${escape(item.required ? 'required' : 'optional')}</div><div class="item-summary">${escape(item.detail)}</div></li>`).join('');
    }
    function renderWatchlistBoard(board) {
      const items = board?.items || [];
      $('watchlistMeta').textContent = board ? `${board.researched}/${board.total} researched` : '';
      $('watchlistBoard').innerHTML = items.length ? items.map(item => `<tr><td><button class="watch-symbol" type="button" data-watch-symbol="${escape(item.symbol)}">${escape(item.symbol)}</button></td><td>${escape(item.last_close == null ? 'N/A' : money(item.last_close, item.currency))}</td><td>${escape(item.last_price_date || 'N/A')}</td><td><span class="board-status ${escape(item.data_status)}">${escape(item.data_status)}</span></td><td>${escape(item.latest_research_at ? item.latest_research_at.slice(0,16).replace('T', ' ') : 'None')}</td><td>${escape(item.risk_decision || item.decision || 'None')}</td></tr>`).join('') : '<tr><td class="empty" colspan="6">No watchlist symbols yet.</td></tr>';
    }
    function renderChart(market) {
      if (!market || market.series.length < 2) { $('chart').innerHTML = '<div class="empty">No cached price series available.</div>'; $('chartMeta').textContent = ''; return; }
      const values = market.series.map(point => point.close), min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, Math.max(max, 1) * .03), width = 720, height = 220, pad = 12;
      const points = market.series.map((point, index) => `${pad + index * ((width - pad * 2) / (market.series.length - 1))},${height - pad - ((point.close - min) / span) * (height - pad * 2)}`).join(' ');
      $('chart').innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Cached closing price history"><line x1="${pad}" y1="${height - pad}" x2="${width-pad}" y2="${height-pad}" stroke="#d5dfe3"/><line x1="${pad}" y1="${pad}" x2="${width-pad}" y2="${pad}" stroke="#e4ebed"/><polyline fill="none" stroke="#176f6c" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" points="${points}"/></svg>`;
      $('chartMeta').textContent = `${market.first_date} to ${market.last_date}`;
    }
    function renderCompanyProfile(profile) {
      const target = $('companyProfile');
      if (!profile || !profile.available) { target.innerHTML = '<div class="empty">No vendor-supplied company profile available.</div>'; $('companyProfileMeta').textContent = ''; return; }
      const fields = [['Name', profile.name], ['Industry', profile.industry], ['Area', profile.area], ['Market', profile.market], ['Exchange', profile.exchange], ['Listing Date', profile.list_date]].filter(([, value]) => value);
      target.innerHTML = fields.map(([label, value]) => `<div><span class="label">${escape(label)}</span><span class="value">${escape(value)}</span></div>`).join('');
      $('companyProfileMeta').textContent = `Available as of ${profile.as_of_date}`;
    }
    function renderFundamentals(fundamentals) {
      if (!fundamentals) { $('fundamentals').innerHTML = '<div class="empty">No cached fundamentals available.</div>'; $('fundamentalsMeta').textContent = ''; return; }
      const entries = Object.entries(fundamentals.metrics || {}).slice(0, 12);
      $('fundamentals').innerHTML = entries.length ? entries.map(([key, value]) => `<div><span class="label">${escape(key.replaceAll('_', ' '))}</span><span class="value">${escape(typeof value === 'number' ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 4}) : text(value))}</span></div>`).join('') : '<div class="empty">Latest snapshot has no metrics.</div>';
      $('fundamentalsMeta').textContent = `As of ${fundamentals.provenance.as_of_date}`;
    }
    function renderValuationContext(context) {
      const target = $('valuationContext');
      if (!context || !context.available) {
        target.innerHTML = '<tr><td colspan="7" class="empty">Fewer than 20 valid cached daily valuation observations are available.</td></tr>';
        $('valuationContextMeta').textContent = '';
        return;
      }
      const rows = context.metrics.filter(item => item.available);
      target.innerHTML = rows.map(item => `<tr><td>${escape(item.label)}</td><td>${escape(text(item.latest))}</td><td>${escape(Number(item.percentile).toFixed(1))}%</td><td>${escape(text(item.low))}</td><td>${escape(text(item.median))}</td><td>${escape(text(item.high))}</td><td>${escape(text(item.observations))}</td></tr>`).join('');
      $('valuationContextMeta').textContent = `${context.daily_snapshot_count} cached days through ${context.as_of_date}`;
    }
    function renderFinancialQuality(snapshot) {
      if (!snapshot) { $('financialQuality').innerHTML = '<div class="empty">No disclosed financial quality snapshot available.</div>'; $('financialQualityMeta').textContent = ''; return; }
      const entries = Object.entries(snapshot.metrics || {}).slice(0, 12);
      $('financialQuality').innerHTML = entries.length ? entries.map(([key, value]) => `<div><span class="label">${escape(key.replaceAll('_', ' '))}</span><span class="value">${escape(typeof value === 'number' ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 4}) : text(value))}</span></div>`).join('') : '<div class="empty">Financial quality snapshot has no metrics.</div>';
      $('financialQualityMeta').textContent = `Report period ${snapshot.period_end}`;
    }
    function renderFinancialHealth(assessment) {
      $('financialHealthMeta').textContent = `${assessment.status} - ${assessment.score}/4 checks`;
      $('financialHealth').innerHTML = assessment.checks.map(check => `<li class="item"><div class="item-title">${escape(check.name.replaceAll('_', ' '))}</div><div class="item-meta">${escape(check.status)} | ${escape(text(check.observed))} / ${escape(text(check.threshold))}</div><div class="item-summary">${escape(check.message)}</div></li>`).join('');
    }
    function renderFinancialTrend(items) {
      $('financialTrendMeta').textContent = `${items.length} disclosed periods`;
      $('financialTrend').innerHTML = items.length ? items.map(item => {
        const metrics = item.metrics || {};
        const format = value => typeof value === 'number' ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 4}) : text(value);
        return `<tr><td>${escape(item.period_end)}</td><td>${escape(format(metrics.reported_total_revenue))}</td><td>${escape(format(metrics.reported_net_income))}</td><td>${escape(format(metrics.reported_operating_cashflow))}</td><td>${escape(format(metrics.return_on_equity_pct))}</td></tr>`;
      }).join('') : '<tr><td colspan="5" class="empty">Fewer than two disclosed periods are available.</td></tr>';
    }
    function renderAgents(outputs) {
      $('agentsMeta').textContent = `${outputs.length} available`;
      $('agents').innerHTML = outputs.length ? outputs.map(output => `<li class="item"><div class="item-title">${escape(output.headline)}</div><div class="item-meta">${escape(output.agent_role)} - ${escape(output.output_type)} - ${escape(output.as_of_date)}</div><div class="item-summary">${escape(output.summary)}</div>${output.risks && output.risks.length ? `<div class="tags">${output.risks.slice(0,3).map(risk => `<span class="tag">Risk: ${escape(risk)}</span>`).join('')}</div>` : ''}</li>`).join('') : '<li class="empty">No structured agent outputs available.</li>';
    }
    function renderNews(items) {
      $('newsMeta').textContent = `${items.length} latest`;
      $('news').innerHTML = items.length ? items.map(item => `<li class="item"><div class="item-title">${item.url ? `<a href="${escape(item.url)}" target="_blank" rel="noreferrer">${escape(item.title)}</a>` : escape(item.title)}</div><div class="item-meta">${escape(item.provider)} - ${escape(item.published_at.slice(0,10))}</div>${item.summary ? `<div class="item-summary">${escape(item.summary)}</div>` : ''}</li>`).join('') : '<li class="empty">No cached news available.</li>';
    }
    function renderDecision(run) {
      if (!run || !run.signal) { $('decision').innerHTML = '<div class="empty">No archived trade decision available.</div>'; $('decisionMeta').textContent = ''; return; }
      const signal = run.signal, review = run.risk_review;
      const rows = [
        ['Signal', signal.direction], ['Horizon', signal.horizon], ['Confidence', pct(signal.confidence)], ['Proposed Position', pct(signal.proposed_position_pct)],
        ['Risk Decision', review ? review.decision : 'Not reviewed'], ['Approved Position', review ? pct(review.approved_position_pct) : 'N/A'],
        ['Risk Breaches', review ? review.breaches.length : 'N/A'], ['Run As Of', run.as_of_date.slice(0, 10)]
      ];
      $('decision').innerHTML = rows.map(([label, value]) => `<div><span class="label">${escape(label)}</span><span class="value">${escape(value)}</span></div>`).join('');
      $('decisionMeta').textContent = `Generated ${run.generated_at.slice(0,16).replace('T', ' ')}`;
    }
    function clearReportWorkspace(message) {
      $('openReport').removeAttribute('href'); $('openReport').setAttribute('aria-disabled', 'true');
      $('exportReport').removeAttribute('href'); $('exportReport').setAttribute('aria-disabled', 'true');
      $('reportCoverage').className = 'empty'; $('reportCoverage').textContent = message;
      $('reportPreview').textContent = 'No archived report available.';
    }
    async function renderReportWorkspace(snapshot) {
      const run = snapshot.latest_run, workspace = snapshot.report_workspace;
      if (!run || !workspace?.available) { clearReportWorkspace('Select an archived research run to view coverage.'); return; }
      const baseUrl = `/api/reports/${encodeURIComponent(snapshot.symbol)}/${encodeURIComponent(run.run_id)}.md`;
      $('openReport').href = baseUrl; $('openReport').setAttribute('aria-disabled', 'false');
      $('exportReport').href = `${baseUrl}?download=1`; $('exportReport').setAttribute('aria-disabled', 'false');
      $('exportReport').setAttribute('download', `${snapshot.symbol}_${run.run_id}.md`);
      $('reportCoverage').className = '';
      $('reportCoverage').innerHTML = `<div class="coverage-summary">Core data coverage: ${workspace.core_available}/${workspace.core_total}</div><ul class="coverage-list">${workspace.items.map(item => `<li class="coverage-item"><div><div class="coverage-label">${escape(item.label)}</div><div class="coverage-detail">${escape(item.detail)}</div></div><span class="coverage-status ${item.available ? '' : 'missing'}">${item.available ? 'Available' : item.optional ? 'Not used' : 'Missing'}</span></li>`).join('')}</ul>`;
      try {
        const report = await fetch(baseUrl).then(response => response.ok ? response.text() : Promise.reject(response));
        $('reportPreview').textContent = report;
      } catch (error) { $('reportPreview').textContent = 'Unable to load the archived report.'; }
    }
    function renderBacktest(run) {
      if (!run || !run.backtest) { $('backtest').innerHTML = '<div class="empty">No archived backtest available.</div>'; $('backtestMeta').textContent = ''; return; }
      const backtest = run.backtest, metrics = backtest.metrics;
      const rows = [
        ['Total Return', pct(metrics.total_return_pct)], ['Max Drawdown', pct(metrics.max_drawdown_pct)], ['Sharpe', text(metrics.sharpe == null ? null : Number(metrics.sharpe).toFixed(2))], ['Win Rate', pct(metrics.win_rate_pct)],
        ['Profit Factor', text(metrics.profit_factor == null ? null : Number(metrics.profit_factor).toFixed(2))], ['Trades', backtest.trade_count], ['Closed Round Trips', backtest.round_trip_count], ['Warnings', backtest.warning_count]
      ];
      $('backtest').innerHTML = rows.map(([label, value]) => `<div><span class="label">${escape(label)}</span><span class="value">${escape(value)}</span></div>`).join('');
      $('backtestMeta').textContent = 'Latest archived run';
    }
    let activeRunId = null;
    let activeJobId = null;
    let activeBatchJobIds = [];
    let watchlistSymbols = new Set();
    function renderRunHistory(runs, selectedRunId) {
      const selector = $('runHistory');
      if (!runs.length) {
        selector.innerHTML = '<option value="">No archived runs</option>';
        selector.disabled = true;
        $('runHistoryDetail').textContent = 'No archived research runs available.';
        return;
      }
      selector.disabled = false;
      selector.innerHTML = '<option value="">Latest archived run</option>' + runs.map(run => `<option value="${escape(run.run_id)}">${escape(run.as_of_date.slice(0,10))} - ${escape(run.generated_at.slice(0,16).replace('T', ' '))}</option>`).join('');
      selector.value = selectedRunId || '';
      const selected = runs.find(run => run.run_id === selectedRunId) || runs[0];
      const scopes = [selected.has_signal ? 'signal' : null, selected.has_risk_review ? 'risk' : null, selected.has_backtest ? 'backtest' : null].filter(Boolean);
      $('runHistoryDetail').textContent = `${runs.length} archived run${runs.length === 1 ? '' : 's'} - ${scopes.join(', ') || 'data snapshot'}`;
    }
    function setResearchButton(isRunning) {
      $('runResearch').disabled = isRunning || !$('symbol').value;
      $('runResearch').textContent = isRunning ? 'Research Running' : 'Run Research';
      $('refreshWatchlistResearch').disabled = isRunning;
      $('refreshWatchlistResearch').textContent = isRunning && activeBatchJobIds.length ? 'Refreshing Watchlist' : 'Refresh Watchlist';
    }
    async function pollWatchlistRefresh() {
      if (!activeBatchJobIds.length) return;
      try {
        const payloads = await Promise.all(activeBatchJobIds.map(jobId => fetch(`/api/research-jobs/${encodeURIComponent(jobId)}`).then(response => response.ok ? response.json() : Promise.reject(response))));
        const jobs = payloads.map(payload => payload.job);
        const pending = jobs.filter(job => job.status === 'queued' || job.status === 'running');
        const completed = jobs.length - pending.length;
        $('status').textContent = `Watchlist refresh: ${completed}/${jobs.length} completed.`;
        if (pending.length) { window.setTimeout(pollWatchlistRefresh, 800); return; }
        const failed = jobs.filter(job => job.status === 'failed');
        activeBatchJobIds = [];
        setResearchButton(false);
        $('status').textContent = failed.length ? `Watchlist refresh completed with ${failed.length} failed job${failed.length === 1 ? '' : 's'}.` : `Watchlist refresh completed for ${jobs.length} symbol${jobs.length === 1 ? '' : 's'}.`;
        await refreshSymbols();
        await loadSnapshot();
      } catch (error) {
        activeBatchJobIds = [];
        setResearchButton(false);
        $('status').textContent = 'Unable to refresh the watchlist.';
      }
    }
    async function startWatchlistRefresh() {
      if (activeJobId || activeBatchJobIds.length) return;
      setResearchButton(true);
      try {
        const payload = await fetch('/api/watchlist-refresh', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({data_provider: $('dataProvider').value, narrative_mode: $('narrativeMode').value})}).then(response => response.ok ? response.json() : Promise.reject(response));
        activeBatchJobIds = payload.jobs.map(job => job.job_id);
        if (!activeBatchJobIds.length) { setResearchButton(false); $('status').textContent = 'Watchlist is empty.'; return; }
        await pollWatchlistRefresh();
      } catch (error) {
        activeBatchJobIds = [];
        setResearchButton(false);
        $('status').textContent = 'Unable to start the watchlist refresh.';
      }
    }
    async function pollResearchJob() {
      if (!activeJobId) return;
      const payload = await fetch(`/api/research-jobs/${encodeURIComponent(activeJobId)}`).then(response => response.ok ? response.json() : Promise.reject(response));
      const job = payload.job;
      if (job.status === 'queued' || job.status === 'running') {
        $('status').textContent = `Research job ${job.status} for ${job.request.symbol}.`;
        window.setTimeout(pollResearchJob, 800);
        return;
      }
      activeJobId = null;
      setResearchButton(false);
      if (job.status === 'succeeded') {
        $('status').textContent = `Research completed for ${job.request.symbol}.`;
        await refreshSymbols(job.request.symbol);
        await loadSnapshot();
      } else {
        $('status').textContent = `Research failed: ${job.error || 'provider unavailable'}`;
      }
    }
    function manualSignalPayload() {
      const direction = $('decisionDirection').value;
      if (!direction) return null;
      const confidence = Number($('decisionConfidence').value) / 100;
      const position = Number($('decisionPosition').value) / 100;
      if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1 || !Number.isFinite(position) || position < 0 || position > 1) {
        throw new Error('Decision percentages must be between 0 and 100.');
      }
      const rationale = $('decisionRationale').value.trim();
      if (!rationale) throw new Error('A decision rationale is required.');
      return {
        direction,
        horizon: $('decisionHorizon').value,
        confidence,
        proposed_position_pct: position,
        rationale
      };
    }    async function startResearch() {
      const symbol = $('symbol').value;
      if (!symbol || activeJobId) return;
      setResearchButton(true);
      try {
        const payload = await fetch('/api/research-jobs', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({symbol, data_provider: $('dataProvider').value, narrative_mode: $('narrativeMode').value, manual_signal: manualSignalPayload()})}).then(response => response.ok ? response.json() : Promise.reject(response));
        activeJobId = payload.job.job_id;
        await pollResearchJob();
      } catch (error) {
        activeJobId = null;
        setResearchButton(false);
        $('status').textContent = 'Unable to start local research.';
      }
    }
    async function refreshSymbols(preferredSymbol) {
      const payload = await fetch('/api/symbols').then(response => response.json());
      const current = preferredSymbol || $('symbol').value;
      $('symbol').innerHTML = payload.symbols.length ? payload.symbols.map(symbol => `<option value="${escape(symbol)}">${escape(symbol)}</option>`).join('') : '<option value="">No symbols</option>';
      if (payload.symbols.includes(current)) $('symbol').value = current;
    }
    async function refreshWatchlistBoard() {
      const payload = await fetch('/api/watchlist-board').then(response => response.json());
      renderWatchlistBoard(payload);
    }
    async function refreshWatchlist() {
      const payload = await fetch('/api/watchlist').then(response => response.json());
      watchlistSymbols = new Set(payload.entries.map(entry => entry.symbol));
      $('removeSymbol').disabled = !watchlistSymbols.has($('symbol').value);
    }
    async function loadSnapshot() {
      const symbol = $('symbol').value;
      if (!symbol) { $('status').textContent = 'No watched or cached ticker is available.'; renderMetrics({artifact_counts:{}}); renderDataHealth(null); renderReadiness(null); renderChart(null); renderCompanyProfile(null); renderFundamentals(null); renderValuationContext(null); renderFinancialQuality(null); renderFinancialHealth({status:"unknown",score:0,checks:[]}); renderFinancialTrend([]); renderAgents([]); renderNews([]); renderDecision(null); renderBacktest(null); renderRunHistory([], null); clearReportWorkspace('Select an archived research run to view coverage.'); await refreshWatchlistBoard(); return; }
      $('status').textContent = `Loading ${symbol} from local research storage...`;
      try {
        const runQuery = activeRunId ? `&run_id=${encodeURIComponent(activeRunId)}` : '';
        const snapshot = await fetch(`/api/snapshot?symbol=${encodeURIComponent(symbol)}${runQuery}`).then(response => response.ok ? response.json() : Promise.reject(response));
        $('title').textContent = `${snapshot.symbol} Research Cockpit`;
        $('status').textContent = snapshot.has_data ? `Local artifacts loaded for ${snapshot.symbol}. No external data request was made.` : `No artifacts found for ${snapshot.symbol}.`;
        renderMetrics(snapshot); renderDataHealth(snapshot.data_health); renderReadiness(snapshot.research_readiness); renderChart(snapshot.market); renderCompanyProfile(snapshot.company_profile); renderFundamentals(snapshot.fundamentals); renderValuationContext(snapshot.valuation_context); renderFinancialQuality(snapshot.financial_quality); renderFinancialHealth(snapshot.financial_health); renderFinancialTrend(snapshot.financial_quality_history); renderAgents(snapshot.agent_outputs); renderNews(snapshot.news); renderDecision(snapshot.latest_run); renderBacktest(snapshot.latest_run); renderRunHistory(snapshot.runs, activeRunId); await renderReportWorkspace(snapshot);
        await refreshWatchlist();
        await refreshWatchlistBoard();
        setResearchButton(Boolean(activeJobId));
      } catch (error) { $('status').textContent = 'Unable to load local research storage.'; }
    }
    async function addToWatchlist() {
      const symbol = $('watchSymbol').value.trim();
      if (!symbol) return;
      await fetch('/api/watchlist', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({symbol})});
      $('watchSymbol').value = '';
      activeRunId = null;
      await refreshSymbols(symbol.toUpperCase());
      await loadSnapshot();
    }
    async function removeFromWatchlist() {
      const symbol = $('symbol').value;
      if (!symbol) return;
      await fetch(`/api/watchlist?symbol=${encodeURIComponent(symbol)}`, {method:'DELETE'});
      activeRunId = null;
      await refreshSymbols();
      await loadSnapshot();
    }
    async function start() {
      try {
        await refreshSymbols();
        await refreshWatchlist();
        await refreshWatchlistBoard();
        setResearchButton(Boolean(activeJobId));
      } catch (error) { $('symbol').innerHTML = '<option value="">Storage unavailable</option>'; }
      await loadSnapshot();
    }
    $('runResearch').addEventListener('click', startResearch);
    $('refresh').addEventListener('click', loadSnapshot);
    $('symbol').addEventListener('change', async () => { activeRunId = null; await loadSnapshot(); });
    $('runHistory').addEventListener('change', async () => { activeRunId = $('runHistory').value || null; await loadSnapshot(); });
    $('addSymbol').addEventListener('click', addToWatchlist);
    $('removeSymbol').addEventListener('click', removeFromWatchlist);
    $('watchlistBoard').addEventListener('click', async event => { const button = event.target.closest('[data-watch-symbol]'); if (!button) return; activeRunId = null; await refreshSymbols(button.dataset.watchSymbol); await loadSnapshot(); });
    $('watchSymbol').addEventListener('keydown', async event => { if (event.key === 'Enter') await addToWatchlist(); });
    start();
  </script>
</body>
</html>"""


if __name__ == "__main__":
    raise SystemExit(main())
