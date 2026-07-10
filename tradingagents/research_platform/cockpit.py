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

from .artifact_store import JsonArtifactStore
from .run_archive import JsonResearchRunArchive

_EARLIEST_DATE = date(1900, 1, 1)


def discover_cached_symbols(store: JsonArtifactStore) -> list[str]:
    """Return symbols which have at least one cached artifact file."""

    symbols: set[str] = set()
    for kind in ("prices", "fundamentals", "news", "agent_outputs"):
        directory = store.root / kind
        if directory.exists():
            symbols.update(path.stem for path in directory.glob("*.jsonl"))
    runs_directory = store.root / "runs"
    if runs_directory.exists():
        symbols.update(path.name for path in runs_directory.iterdir() if path.is_dir())
    return sorted(symbols)


def build_cockpit_snapshot(store: JsonArtifactStore, symbol: str) -> dict[str, Any]:
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
    latest_run = JsonResearchRunArchive(store.root).load_latest_bundle(normalized_symbol)
    return {
        "symbol": normalized_symbol,
        "has_data": bool(bars or fundamentals or news or agent_outputs or latest_run),
        "market": market,
        "fundamentals": (
            latest_fundamentals.model_dump(mode="json") if latest_fundamentals is not None else None
        ),
        "news": [item.model_dump(mode="json") for item in news[:12]],
        "agent_outputs": [item.model_dump(mode="json") for item in agent_outputs[:12]],
        "latest_run": _run_summary(latest_run),
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



def _run_summary(bundle: Any | None) -> dict[str, Any] | None:
    if bundle is None:
        return None

    backtest = bundle.backtest_result
    return {
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

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(HTTPStatus.OK, _APP_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed.path == "/api/symbols":
            self._send_json(HTTPStatus.OK, {"symbols": discover_cached_symbols(self.store)})
            return
        if parsed.path == "/api/snapshot":
            symbol = parse_qs(parsed.query).get("symbol", [""])[0]
            try:
                self._send_json(HTTPStatus.OK, build_cockpit_snapshot(self.store, symbol))
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep normal browser navigation out of the terminal."""

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send_text(status, json.dumps(payload, ensure_ascii=True), "application/json; charset=utf-8")

    def _send_text(self, status: HTTPStatus, payload: str, content_type: str) -> None:
        encoded = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
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

    class BoundCockpitRequestHandler(CockpitRequestHandler):
        pass

    BoundCockpitRequestHandler.store = store
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


_APP_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Research Cockpit</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #15212b; background: #f4f7f8; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #f4f7f8; }
    button, select { font: inherit; }
    .shell { max-width: 1440px; margin: 0 auto; padding: 28px 32px 44px; }
    .topbar { display: flex; justify-content: space-between; gap: 20px; align-items: end; border-bottom: 1px solid #d5dfe3; padding-bottom: 20px; }
    h1 { margin: 0; font-size: 25px; line-height: 1.2; font-weight: 700; letter-spacing: 0; }
    .eyebrow { margin: 0 0 7px; color: #39706e; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
    .control { display: flex; gap: 8px; align-items: center; }
    select, button { height: 36px; border: 1px solid #b8c7cd; border-radius: 5px; background: #fff; color: #15212b; padding: 0 11px; }
    select { min-width: 150px; }
    button { cursor: pointer; font-weight: 650; }
    button:hover { border-color: #39706e; background: #edf8f7; }
    .status { min-height: 20px; color: #64747d; font-size: 13px; margin: 16px 0 12px; }
    .metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); border: 1px solid #d5dfe3; border-radius: 6px; background: #fff; }
    .metric { min-height: 102px; padding: 18px; border-right: 1px solid #d5dfe3; }
    .metric:last-child { border-right: 0; }
    .metric-label { color: #64747d; font-size: 12px; font-weight: 650; text-transform: uppercase; letter-spacing: .06em; }
    .metric-value { margin-top: 9px; font-size: 24px; font-weight: 700; overflow-wrap: anywhere; }
    .metric-detail { margin-top: 5px; color: #64747d; font-size: 12px; }
    .positive { color: #087f5b; } .negative { color: #bf3f46; } .neutral { color: #15212b; }
    .workspace { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(320px, .85fr); gap: 18px; margin-top: 18px; }
    .panel { background: #fff; border: 1px solid #d5dfe3; border-radius: 6px; overflow: hidden; }
    .panel-title { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; padding: 15px 17px; border-bottom: 1px solid #d5dfe3; }
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
    .empty { padding: 40px 18px; color: #64747d; text-align: center; } a { color: #176f6c; }
    @media (max-width: 860px) { .shell { padding: 20px 16px 32px; } .topbar { align-items: stretch; flex-direction: column; } .metrics { grid-template-columns: 1fr 1fr; } .metric:nth-child(2) { border-right: 0; } .metric:nth-child(-n+2) { border-bottom: 1px solid #d5dfe3; } .workspace { grid-template-columns: 1fr; } }
    @media (max-width: 460px) { .control { width: 100%; } select { flex: 1; min-width: 0; } .metrics { grid-template-columns: 1fr; } .metric { border-right: 0; border-bottom: 1px solid #d5dfe3; } .metric:last-child { border-bottom: 0; } .grid { grid-template-columns: 1fr; } .grid > div:nth-child(odd) { border-right: 0; } }
  </style>
</head>
<body>
  <main class="shell">
    <div class="topbar">
      <div><p class="eyebrow">Local-first equity research</p><h1 id="title">Research Cockpit</h1></div>
      <div class="control"><select id="symbol" aria-label="Ticker symbol"></select><button id="refresh" type="button">Refresh</button></div>
    </div>
    <p class="status" id="status">Loading local research cache...</p>
    <section class="metrics" aria-label="Market summary" id="metrics"></section>
    <section class="workspace">
      <div class="panel"><div class="panel-title"><h2>Price History</h2><span class="panel-meta" id="chartMeta"></span></div><div class="chart" id="chart"></div></div>
      <div class="panel"><div class="panel-title"><h2>Latest Fundamentals</h2><span class="panel-meta" id="fundamentalsMeta"></span></div><div class="grid" id="fundamentals"></div></div>
      <div class="panel"><div class="panel-title"><h2>Structured Research</h2><span class="panel-meta" id="agentsMeta"></span></div><ul class="items" id="agents"></ul></div>
      <div class="panel"><div class="panel-title"><h2>News</h2><span class="panel-meta" id="newsMeta"></span></div><ul class="items" id="news"></ul></div>
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
    function renderChart(market) {
      if (!market || market.series.length < 2) { $('chart').innerHTML = '<div class="empty">No cached price series available.</div>'; $('chartMeta').textContent = ''; return; }
      const values = market.series.map(point => point.close), min = Math.min(...values), max = Math.max(...values), span = Math.max(max - min, Math.max(max, 1) * .03), width = 720, height = 220, pad = 12;
      const points = market.series.map((point, index) => `${pad + index * ((width - pad * 2) / (market.series.length - 1))},${height - pad - ((point.close - min) / span) * (height - pad * 2)}`).join(' ');
      $('chart').innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Cached closing price history"><line x1="${pad}" y1="${height - pad}" x2="${width-pad}" y2="${height-pad}" stroke="#d5dfe3"/><line x1="${pad}" y1="${pad}" x2="${width-pad}" y2="${pad}" stroke="#e4ebed"/><polyline fill="none" stroke="#176f6c" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" points="${points}"/></svg>`;
      $('chartMeta').textContent = `${market.first_date} to ${market.last_date}`;
    }
    function renderFundamentals(fundamentals) {
      if (!fundamentals) { $('fundamentals').innerHTML = '<div class="empty">No cached fundamentals available.</div>'; $('fundamentalsMeta').textContent = ''; return; }
      const entries = Object.entries(fundamentals.metrics || {}).slice(0, 12);
      $('fundamentals').innerHTML = entries.length ? entries.map(([key, value]) => `<div><span class="label">${escape(key.replaceAll('_', ' '))}</span><span class="value">${escape(typeof value === 'number' ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 4}) : text(value))}</span></div>`).join('') : '<div class="empty">Latest snapshot has no metrics.</div>';
      $('fundamentalsMeta').textContent = `As of ${fundamentals.provenance.as_of_date}`;
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
    async function loadSnapshot() {
      const symbol = $('symbol').value;
      if (!symbol) { $('status').textContent = 'No cached ticker found. Run the local research workflow with an artifact store first.'; renderMetrics({artifact_counts:{}}); renderChart(null); renderFundamentals(null); renderAgents([]); renderNews([]); renderDecision(null); renderBacktest(null); return; }
      $('status').textContent = `Loading ${symbol} from local cache...`;
      try {
        const snapshot = await fetch(`/api/snapshot?symbol=${encodeURIComponent(symbol)}`).then(response => response.ok ? response.json() : Promise.reject(response));
        $('title').textContent = `${snapshot.symbol} Research Cockpit`;
        $('status').textContent = snapshot.has_data ? `Local cache loaded for ${snapshot.symbol}. No external data request was made.` : `No artifacts found for ${snapshot.symbol}.`;
        renderMetrics(snapshot); renderChart(snapshot.market); renderFundamentals(snapshot.fundamentals); renderAgents(snapshot.agent_outputs); renderNews(snapshot.news); renderDecision(snapshot.latest_run); renderBacktest(snapshot.latest_run);
      } catch (error) { $('status').textContent = 'Unable to load the local research cache.'; }
    }
    async function start() {
      try {
        const payload = await fetch('/api/symbols').then(response => response.json());
        $('symbol').innerHTML = payload.symbols.length ? payload.symbols.map(symbol => `<option value="${escape(symbol)}">${escape(symbol)}</option>`).join('') : '<option value="">No cached symbols</option>';
      } catch (error) { $('symbol').innerHTML = '<option value="">Cache unavailable</option>'; }
      await loadSnapshot();
    }
    $('refresh').addEventListener('click', loadSnapshot); $('symbol').addEventListener('change', loadSnapshot); start();
  </script>
</body>
</html>'''


if __name__ == "__main__":
    raise SystemExit(main())
