# Ops Dashboard React UI + opsdash.test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the vanilla-JS dashboard frontend with a React implementation of the approved `Ops Dashboard.dc.html` design, and make it reachable at `http://opsdash.test`.

**Architecture:** A Vite + React 18 + TypeScript SPA in a new `dashboard-ui/` directory builds into `ops/dashboard/static/`, which the existing loopback-only Python server already serves — Node is build-time only. One small backend change adds the short + insider journals to `/api/events`. A one-time sudo script maps `opsdash.test` in `/etc/hosts` and installs a pf redirect (loopback :80 → :8321).

**Tech Stack:** React 18, TypeScript ~5.6, Vite 6, Vitest 2 (frontend); pytest (server); pf + launchd (hostname).

**Spec:** `docs/superpowers/specs/2026-07-14-opsdash-react-ui-design.md`
**Visual source of truth:** `dashboard-ui/design/ops-dashboard.dc.html` (committed in Task 2) — exact colors, spacing, and layout come from there; the CSS in Task 5 is its translation.

## Global Constraints

- **Money is decimal strings end-to-end.** The API serializes `Decimal` as strings; never `parseFloat`/`Number()` money for display. `Number()` is allowed ONLY for sparkline plotting geometry and pnl *ratios* (they are not money).
- **No `dangerouslySetInnerHTML` anywhere.** Journal payloads carry arbitrary operator/ticker text.
- **`ops/dashboard/server.py` keeps `_HOST = "127.0.0.1"` untouched.** Nothing becomes network-reachable.
- **Per-section error isolation:** any section/sleeve shaped `{"error": ...}` renders an UNAVAIL chip; the page never goes blank because one store is missing.
- Built assets are committed to `ops/dashboard/static/` with **stable (unhashed) filenames** (server sends `Cache-Control: no-store`, so no cache-busting is needed).
- Python test scope is `pytest tests/ops/dashboard/ -v` (there are 11 known-failing `test_main.py` tests on main — unrelated; do not chase them).
- Frontend commands run from `dashboard-ui/`: `npm test` (vitest), `npm run build` (tsc + vite).
- Commit messages follow repo style (`feat(dashboard): …`, `fix: …`) and end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## Snapshot payload reference (all tasks)

`GET /api/snapshot` (shapes actually produced by `ops/dashboard/snapshot.py`; every top-level section and every sleeve may instead be `{"error": "..."}`):

```json
{
  "generated_at": "2026-07-14T18:00:00+00:00",
  "health": {
    "verdict": "RUNNING",
    "broker_mode": "paper",
    "guardian": {"alive_at": "…", "age_seconds": 42.1},
    "halts": {"daily_halt_today": false, "kill_switch_this_week": false},
    "research_paused": false,
    "heartbeat_errors_24h": 0
  },
  "sleeves": {
    "momentum": {
      "equity": "10450.2347", "cash": "2140.55",
      "equity_at": "…", "equity_kind": "close",
      "day_pnl_pct": "0.0182", "lifetime_pnl_pct": "0.0450",
      "series": [{"at": "…", "equity": "10450.2347"}],
      "positions": [{"symbol": "AAPL", "quantity": "12", "entry": "214.30", "stop": "205.00"}],
      "fills_today": [{"symbol": "AAPL", "side": "buy", "quantity": "6", "price": "214.28", "filled_at": "…"}]
    },
    "research": {}, "baseline": {}, "short": {}, "insider": {}
  },
  "funnel": {
    "screener": {"last_run": {"run_id": "…", "asof": "2026-07-13", "created_at": "…", "universe_size": 5000, "passed_count": 37}, "hits_by_status": {"pending": 3}},
    "memos": {"by_status": {"open": 4, "closed": 12}, "open": [{"memo_id": "…", "ticker": "NVDA", "thesis_type": "ai capex durability", "conviction_tier": 1, "created_at": "…", "status": "open"}]},
    "overnight": {"last_vetting_run": {"at": "…", "age_seconds": 29532.0, "payload": {}}, "last_drain_run": null, "paused": false},
    "signals_7d": {"falsifier_tripped": 0, "research_escalation": 1, "resolution_due": 0, "catalyst_due": 2}
  },
  "anomalies_7d": {"guardian_check_error": {"count": 0, "last_at": null}},
  "market": {"is_open": true, "next_open": "…", "previous_close": "…", "is_trading_day": true}
}
```

`GET /api/events?limit=100` → `[{"source": "momentum", "id": 812, "at": "2026-07-14T17:59:01+00:00", "kind": "fill", "text": "BUY 5 TSLA @ $242.10", "payload": {…}}, …]` (newest first).

`GET /api/logs?file=out&lines=200` → `{"file": "out", "text": "…"}` (`file` ∈ {out, err}).

---

### Task 1: `/api/events` merges short + insider journals

**Files:**
- Modify: `ops/dashboard/server.py:94-98` (`_api_events` paths dict)
- Test: `tests/ops/dashboard/test_server.py`

**Interfaces:**
- Produces: `/api/events` items may now carry `"source": "short"` and `"source": "insider"`. Frontend (Task 9) relies on `source` naming exactly matching sleeve names.

- [ ] **Step 1: Split the test fixture so tests can reach the config, and write the failing test**

In `tests/ops/dashboard/test_server.py`, replace the existing `base_url` fixture header (keep the server-thread body identical) so the config is its own fixture, and add the two new journal paths:

```python
@pytest.fixture
def cfg(tmp_path, monkeypatch):
    # (move any monkeypatch lines from the old base_url fixture here unchanged)
    return OpsConfig(
        journal_path=str(tmp_path / "ops.sqlite"),
        baseline_journal_path=str(tmp_path / "baseline.sqlite"),
        research_journal_path=str(tmp_path / "research.sqlite"),
        short_journal_path=str(tmp_path / "short.sqlite"),
        insider_journal_path=str(tmp_path / "insider.sqlite"),
        guardian_liveness_path=str(tmp_path / "guardian.alive"),
        research_pause_flag_path=str(tmp_path / "research.paused"),
    )


@pytest.fixture
def base_url(cfg):
    with Journal(cfg.journal_path) as j:
        j.record_event("service_started", {"pid": 1})
    server = make_server(cfg, port=0)
    # … rest of the old fixture body unchanged …
```

Then append the new test:

```python
def test_events_route_merges_short_and_insider(base_url, cfg):
    with Journal(cfg.short_journal_path) as j:
        j.record_event("fill", {"side": "SHORT", "quantity": "30",
                                "symbol": "GME", "price": "24.10"})
    with Journal(cfg.insider_journal_path) as j:
        j.record_event("fill", {"side": "BUY", "quantity": "15",
                                "symbol": "OKTA", "price": "98.40"})
    status, body = _get(base_url + "/api/events?limit=50")
    assert status == 200
    sources = {e["source"] for e in body}
    assert {"short", "insider"} <= sources
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `python -m pytest tests/ops/dashboard/test_server.py::test_events_route_merges_short_and_insider -v`
Expected: FAIL — `sources` is missing `short`/`insider` (the endpoint only merges three journals).

- [ ] **Step 3: Extend the paths dict in `_api_events`**

In `ops/dashboard/server.py`, the dict at lines 94-98 becomes:

```python
        paths = {
            "momentum": self.config.journal_path,
            "research": self.config.research_journal_path,
            "baseline": self.config.baseline_journal_path,
            "short": self.config.short_journal_path,
            "insider": self.config.insider_journal_path,
        }
```

- [ ] **Step 4: Run the whole dashboard test dir**

Run: `python -m pytest tests/ops/dashboard/ -v`
Expected: all PASS (fixture split must not break the other seven tests).

- [ ] **Step 5: Commit**

```bash
git add ops/dashboard/server.py tests/ops/dashboard/test_server.py
git commit -m "feat(dashboard): /api/events merges short + insider journals

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Scaffold `dashboard-ui/` (Vite + React + TS + Vitest)

**Files:**
- Create: `dashboard-ui/package.json`, `dashboard-ui/tsconfig.json`, `dashboard-ui/vite.config.ts`, `dashboard-ui/index.html`, `dashboard-ui/src/main.tsx`, `dashboard-ui/src/App.tsx` (placeholder), `dashboard-ui/src/app.css` (placeholder), `dashboard-ui/.gitignore`, `dashboard-ui/README.md`
- Commit also: `dashboard-ui/design/ops-dashboard.dc.html` (already extracted to disk — the design reference)

**Interfaces:**
- Produces: `npm run dev` (proxies `/api` → live `127.0.0.1:8321`), `npm run build` (emits `index.html` + `assets/app.js` + `assets/app.css` into `ops/dashboard/static/`), `npm test`.
- Later tasks import from `src/App.tsx` and `src/app.css`; both are fully replaced in Tasks 5–10.

- [ ] **Step 1: Write the config files**

`dashboard-ui/package.json`:

```json
{
  "name": "dashboard-ui",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "test": "vitest run"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "~5.6.2",
    "vite": "^6.0.0",
    "vitest": "^2.1.8"
  }
}
```

`dashboard-ui/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["vite/client"]
  },
  "include": ["src"]
}
```

`dashboard-ui/vite.config.ts` (stable output names per Global Constraints; dev proxy hits the live read-only API on this machine):

```ts
/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../ops/dashboard/static",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/app[extname]",
      },
    },
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8321" },
  },
  test: { environment: "node" },
});
```

`dashboard-ui/.gitignore`:

```
node_modules/
```

`dashboard-ui/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>ops · TradingAgents</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

(Fonts are a CDN nicety exactly as in the design; every `font-family` in Task 5 falls back to `system-ui`/`ui-monospace`, so the dashboard works offline.)

- [ ] **Step 2: Write the entry + placeholders**

`dashboard-ui/src/main.tsx`:

```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./app.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

`dashboard-ui/src/App.tsx` (placeholder — fully replaced in Task 10):

```tsx
export default function App() {
  return <div style={{ padding: 20 }}>ops dashboard — React scaffold</div>;
}
```

`dashboard-ui/src/app.css` (placeholder — fully replaced in Task 5):

```css
body { margin: 0; background: #0c0f14; color: #e8ecf3; }
```

`dashboard-ui/README.md`:

```markdown
# dashboard-ui

React frontend for the local ops dashboard (`ops/dashboard/server.py`).

- `npm ci` — install (build-time only; the deployed service needs no Node)
- `npm run dev` — dev server; proxies `/api` to the live dashboard at 127.0.0.1:8321
- `npm test` — vitest unit tests (pure mappers, poll reducer)
- `npm run build` — typecheck + build into `ops/dashboard/static/` (commit the output)

Visual source of truth: `design/ops-dashboard.dc.html`.
Money is decimal strings end-to-end — never route money through floats.
```

- [ ] **Step 3: Install and verify build + test runner**

Run (from `dashboard-ui/`): `npm install && npm run build && npx vitest run --passWithNoTests`
Expected: build emits `ops/dashboard/static/index.html`, `assets/app.js`, `assets/app.css`; vitest starts and exits 0 (no test files yet — Task 3 adds them).

Then restore the old static files (interim builds must not land in git before cutover):

```bash
git checkout -- ops/dashboard/static/
git clean -fd ops/dashboard/static/
```

- [ ] **Step 4: Run the Python static-serving tests still pass**

Run: `python -m pytest tests/ops/dashboard/test_server.py -v`
Expected: PASS (static dir restored).

- [ ] **Step 5: Commit**

```bash
git add dashboard-ui/package.json dashboard-ui/package-lock.json dashboard-ui/tsconfig.json \
  dashboard-ui/vite.config.ts dashboard-ui/index.html dashboard-ui/.gitignore \
  dashboard-ui/README.md dashboard-ui/src/ dashboard-ui/design/
git commit -m "feat(dashboard-ui): Vite+React+TS scaffold building into ops/dashboard/static

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Types, API client, poll reducer + hook

**Files:**
- Create: `dashboard-ui/src/data/types.ts`, `dashboard-ui/src/data/api.ts`, `dashboard-ui/src/data/poll.ts`
- Test: `dashboard-ui/src/data/poll.test.ts`

**Interfaces:**
- Produces (used by every later task):
  - `types.ts`: `Snapshot`, `Health`, `Sleeve`, `Position`, `Fill`, `SeriesPoint`, `Funnel`, `MemoRow`, `Market`, `AnomalyEntry`, `EventItem`, `Section<T>`, `isErr(x): x is SectionError`, `SLEEVE_ORDER`
  - `api.ts`: `fetchSnapshot(): Promise<Snapshot>`, `fetchEvents(): Promise<EventItem[]>`, `fetchLog(file: "out"|"err"): Promise<{file: string; text: string}>`
  - `poll.ts`: `usePoll(intervalMs?): PollState`, `pollReducer`, `initialPollState`, `isDisconnected(s: PollState): boolean`, `DISCONNECT_AFTER = 3`

- [ ] **Step 1: Write `types.ts`** (fields limited to what the UI consumes; extra API fields simply pass through)

```ts
export interface SectionError { error: string }
export type Section<T> = T | SectionError;

export function isErr(x: unknown): x is SectionError {
  return typeof x === "object" && x !== null && "error" in x;
}

export const SLEEVE_ORDER = ["momentum", "research", "baseline", "short", "insider"] as const;
export type SleeveName = (typeof SLEEVE_ORDER)[number];

export interface Health {
  verdict: "RUNNING" | "STALE" | "STOPPED" | "UNKNOWN";
  broker_mode: string;
  guardian: { alive_at: string | null; age_seconds: number | null };
  halts: { daily_halt_today: boolean; kill_switch_this_week: boolean };
  research_paused: boolean;
}

export interface Market {
  is_open: boolean;
  next_open: string | null;
  previous_close: string | null;
}

export interface Position { symbol: string; quantity: string; entry: string | null; stop: string | null }
export interface Fill { symbol: string; side: string; quantity: string; price: string; filled_at: string }
export interface SeriesPoint { at: string; equity: string }

export interface Sleeve {
  equity: string | null;
  cash: string | null;
  day_pnl_pct: string | null;
  lifetime_pnl_pct: string | null;
  series: SeriesPoint[];
  positions: Position[];
  fills_today: Fill[];
}

export interface MemoRow {
  memo_id: string; ticker: string; thesis_type: string;
  conviction_tier: number | string; created_at: string; status: string;
}

export interface EventView { at: string; age_seconds: number }

export interface Funnel {
  screener: {
    last_run: { asof: string; universe_size: number; passed_count: number } | null;
    hits_by_status: Record<string, number>;
  };
  memos: { by_status: Record<string, number>; open: MemoRow[] };
  overnight: {
    last_vetting_run: EventView | null;
    last_drain_run: EventView | null;
    paused: boolean;
  };
  signals_7d: Record<string, number>;
}

export interface AnomalyEntry { count: number; last_at: string | null }

export interface Snapshot {
  generated_at: string;
  health: Section<Health>;
  sleeves: Section<Record<string, Section<Sleeve>>>;
  funnel: Section<Funnel>;
  anomalies_7d: Section<Record<string, AnomalyEntry>>;
  market: Section<Market>;
}

export interface EventItem {
  source: string; id: number; at: string; kind: string; text: string;
}
```

- [ ] **Step 2: Write `api.ts`**

```ts
import type { EventItem, Snapshot } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export const fetchSnapshot = () => getJson<Snapshot>("/api/snapshot");
export const fetchEvents = () => getJson<EventItem[]>("/api/events?limit=100");
export const fetchLog = (file: "out" | "err") =>
  getJson<{ file: string; text: string }>(`/api/logs?file=${file}&lines=200`);
```

- [ ] **Step 3: Write the failing reducer tests** — `dashboard-ui/src/data/poll.test.ts`

```ts
import { describe, expect, it } from "vitest";
import { DISCONNECT_AFTER, initialPollState, isDisconnected, pollReducer } from "./poll";
import type { Snapshot } from "./types";

const snap = { generated_at: "t" } as Snapshot;

describe("pollReducer", () => {
  it("success stores data and resets failures", () => {
    const failed = pollReducer(initialPollState, { type: "failure" });
    const s = pollReducer(failed, { type: "success", snapshot: snap, events: [], at: 123 });
    expect(s.snapshot).toBe(snap);
    expect(s.failures).toBe(0);
    expect(s.lastGoodAt).toBe(123);
  });

  it("failure keeps last-good data while counting up", () => {
    let s = pollReducer(initialPollState, { type: "success", snapshot: snap, events: [], at: 1 });
    s = pollReducer(s, { type: "failure" });
    expect(s.snapshot).toBe(snap);
    expect(s.failures).toBe(1);
    expect(isDisconnected(s)).toBe(false);
  });

  it(`disconnects after ${DISCONNECT_AFTER} consecutive failures`, () => {
    let s = pollReducer(initialPollState, { type: "success", snapshot: snap, events: [], at: 1 });
    for (let i = 0; i < DISCONNECT_AFTER; i++) s = pollReducer(s, { type: "failure" });
    expect(isDisconnected(s)).toBe(true);
    s = pollReducer(s, { type: "success", snapshot: snap, events: [], at: 2 });
    expect(isDisconnected(s)).toBe(false);
  });
});
```

- [ ] **Step 4: Run tests to verify they fail**

Run (from `dashboard-ui/`): `npm test`
Expected: FAIL — `./poll` has no exports.

- [ ] **Step 5: Write `poll.ts`**

```ts
import { useEffect, useReducer, useRef } from "react";
import { fetchEvents, fetchSnapshot } from "./api";
import type { EventItem, Snapshot } from "./types";

export const DISCONNECT_AFTER = 3;

export interface PollState {
  snapshot: Snapshot | null;
  events: EventItem[];
  lastGoodAt: number | null;
  failures: number;
}

export const initialPollState: PollState = {
  snapshot: null, events: [], lastGoodAt: null, failures: 0,
};

export type PollAction =
  | { type: "success"; snapshot: Snapshot; events: EventItem[]; at: number }
  | { type: "failure" };

export function pollReducer(s: PollState, a: PollAction): PollState {
  if (a.type === "success") {
    return { snapshot: a.snapshot, events: a.events, lastGoodAt: a.at, failures: 0 };
  }
  return { ...s, failures: s.failures + 1 };
}

export const isDisconnected = (s: PollState) => s.failures >= DISCONNECT_AFTER;

export function usePoll(intervalMs = 5000): PollState {
  const [state, dispatch] = useReducer(pollReducer, initialPollState);
  const inFlight = useRef(false); // skip a tick while a fetch is outstanding

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      if (inFlight.current) return;
      inFlight.current = true;
      try {
        const [snapshot, events] = await Promise.all([fetchSnapshot(), fetchEvents()]);
        if (alive) dispatch({ type: "success", snapshot, events, at: Date.now() });
      } catch {
        if (alive) dispatch({ type: "failure" });
      } finally {
        inFlight.current = false;
      }
    };
    void tick();
    const id = setInterval(() => void tick(), intervalMs);
    return () => { alive = false; clearInterval(id); };
  }, [intervalMs]);

  return state;
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `npm test` — Expected: 3 PASS. Also: `npx tsc --noEmit` — Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add dashboard-ui/src/data/
git commit -m "feat(dashboard-ui): snapshot types, API client, polling reducer + hook

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Pure display mappers (money, pct, ages, spark, colors, alerts)

**Files:**
- Create: `dashboard-ui/src/lib/format.ts`, `dashboard-ui/src/lib/spark.ts`, `dashboard-ui/src/lib/colors.ts`, `dashboard-ui/src/lib/alerts.ts`
- Test: `dashboard-ui/src/lib/format.test.ts`, `dashboard-ui/src/lib/alerts.test.ts`, `dashboard-ui/src/lib/spark.test.ts`

**Interfaces:**
- Produces (used by Tasks 5–10):
  - `format.ts`: `fmtMoney(value: string|null|undefined, dp: number): string`, `fmtPct(ratio: string|null|undefined): {text: string; cls: "pos"|"neg"|"flat"}`, `fmtQty(q: string): string`, `relAge(iso: string|null|undefined, nowMs?: number): string`, `hhmmss(iso: string): string`, `guardAge(sec: number|null|undefined): string`
  - `spark.ts`: `sparkPath(values: number[], w: number, h: number): {line: string; area: string}`
  - `colors.ts`: `kindClass(kind: string): string` (CSS class `k-*`), `sideClass(side: string): string` (CSS class `side-*`)
  - `alerts.ts`: `deriveAlert(health: Health|null): {tag: "ALERT"|"NOTICE"; conditions: string[]} | null`

- [ ] **Step 1: Write the failing tests**

`dashboard-ui/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { fmtMoney, fmtPct, fmtQty, guardAge, relAge } from "./format";

describe("fmtMoney (string decimal, never floats)", () => {
  it("rounds half-up and groups thousands", () => {
    expect(fmtMoney("10450.2347", 2)).toBe("$10,450.23");
    expect(fmtMoney("10450.2379", 2)).toBe("$10,450.24");
    expect(fmtMoney("1234567.5", 0)).toBe("$1,234,568");
  });
  it("carries rounding across digits", () => {
    expect(fmtMoney("0.999", 2)).toBe("$1.00");
    expect(fmtMoney("9.995", 2)).toBe("$10.00");
  });
  it("handles negatives, null, and junk", () => {
    expect(fmtMoney("-5.005", 2)).toBe("−$5.01");
    expect(fmtMoney(null, 2)).toBe("—");
    expect(fmtMoney("not-a-number", 2)).toBe("not-a-number");
  });
  it("survives values beyond float precision", () => {
    expect(fmtMoney("90071992547409929.05", 2)).toBe("$90,071,992,547,409,929.05");
  });
});

describe("fmtPct", () => {
  it("scales ratio to percent with sign and class", () => {
    expect(fmtPct("0.0182")).toEqual({ text: "+1.82%", cls: "pos" });
    expect(fmtPct("-0.0064")).toEqual({ text: "−0.64%", cls: "neg" });
    expect(fmtPct("0")).toEqual({ text: "0.00%", cls: "flat" });
    expect(fmtPct(null)).toEqual({ text: "—", cls: "flat" });
  });
});

describe("fmtQty", () => {
  it("strips paper-fill precision noise", () => {
    expect(fmtQty("12.0000")).toBe("12");
    expect(fmtQty("0.5000")).toBe("0.5");
    expect(fmtQty("-30")).toBe("-30");
  });
});

describe("ages", () => {
  const now = Date.parse("2026-07-14T12:00:00Z");
  it("relAge buckets", () => {
    expect(relAge("2026-07-14T11:59:40Z", now)).toBe("just now");
    expect(relAge("2026-07-14T11:30:00Z", now)).toBe("30m ago");
    expect(relAge("2026-07-14T04:00:00Z", now)).toBe("8h ago");
    expect(relAge("2026-07-10T12:00:00Z", now)).toBe("4d ago");
    expect(relAge(null, now)).toBe("—");
  });
  it("guardAge buckets", () => {
    expect(guardAge(42)).toBe("42s");
    expect(guardAge(360)).toBe("6m");
    expect(guardAge(7300)).toBe("2h");
    expect(guardAge(null)).toBe("—");
  });
});
```

`dashboard-ui/src/lib/alerts.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { deriveAlert } from "./alerts";
import type { Health } from "../data/types";

const base: Health = {
  verdict: "RUNNING", broker_mode: "paper",
  guardian: { alive_at: "t", age_seconds: 30 },
  halts: { daily_halt_today: false, kill_switch_this_week: false },
  research_paused: false,
};

describe("deriveAlert", () => {
  it("healthy → no banner", () => {
    expect(deriveAlert(base)).toBeNull();
    expect(deriveAlert(null)).toBeNull();
  });
  it("STOPPED and halts → ALERT with every condition", () => {
    const a = deriveAlert({ ...base, verdict: "STOPPED",
      halts: { daily_halt_today: true, kill_switch_this_week: true } })!;
    expect(a.tag).toBe("ALERT");
    expect(a.conditions).toHaveLength(3);
  });
  it("halt alone still ALERTs while RUNNING", () => {
    const a = deriveAlert({ ...base, halts: { ...base.halts, daily_halt_today: true } })!;
    expect(a.tag).toBe("ALERT");
    expect(a.conditions).toEqual(["daily drawdown halt in effect"]);
  });
  it("STALE / paused → NOTICE", () => {
    expect(deriveAlert({ ...base, verdict: "STALE" })!.tag).toBe("NOTICE");
    expect(deriveAlert({ ...base, research_paused: true })!.conditions)
      .toEqual(["research paused"]);
  });
});
```

`dashboard-ui/src/lib/spark.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { sparkPath } from "./spark";

describe("sparkPath", () => {
  it("builds a line across the full width and a closed area", () => {
    const { line, area } = sparkPath([1, 2, 3], 120, 30);
    expect(line.startsWith("M0")).toBe(true);
    expect(line).toContain("L120");
    expect(area.endsWith("Z")).toBe(true);
  });
  it("flat and empty series do not blow up", () => {
    expect(sparkPath([5, 5, 5], 120, 30).line).toContain("L");
    expect(sparkPath([], 120, 30)).toEqual({ line: "", area: "" });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test` — Expected: FAIL (modules don't exist).

- [ ] **Step 3: Implement**

`dashboard-ui/src/lib/format.ts`:

```ts
// Money display is STRING arithmetic on the API's decimal strings.
// IEEE floats never touch a money value (Global Constraints).

export function fmtMoney(value: string | null | undefined, dp: number): string {
  if (value == null || value === "") return "—";
  let s = String(value);
  const neg = s.startsWith("-");
  if (neg) s = s.slice(1);
  if (!/^\d+(\.\d*)?$/.test(s)) return String(value);
  let [intPart, frac = ""] = s.split(".");
  frac = frac.padEnd(dp + 1, "0");
  const keep = frac.slice(0, dp);
  const roundUp = frac.charCodeAt(dp) - 48 >= 5;
  let digits = intPart + keep;
  if (roundUp) {
    const a = digits.split("");
    let k = a.length - 1;
    while (k >= 0) {
      if (a[k] === "9") { a[k] = "0"; k -= 1; }
      else { a[k] = String(+a[k] + 1); break; }
    }
    if (k < 0) a.unshift("1");
    digits = a.join("");
  }
  let ip = dp ? digits.slice(0, -dp) : digits;
  const fp = dp ? digits.slice(-dp) : "";
  ip = (ip.replace(/^0+(?=\d)/, "") || "0")
    .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return (neg ? "−" : "") + "$" + ip + (dp ? "." + fp : "");
}

export function fmtPct(
  ratio: string | null | undefined,
): { text: string; cls: "pos" | "neg" | "flat" } {
  if (ratio == null || ratio === "") return { text: "—", cls: "flat" };
  const v = Number(ratio) * 100; // a ratio, not money — float is fine
  if (!Number.isFinite(v)) return { text: "—", cls: "flat" };
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  const cls = v > 0 ? "pos" : v < 0 ? "neg" : "flat";
  return { text: sign + Math.abs(v).toFixed(2) + "%", cls };
}

export function fmtQty(q: string): string {
  if (!q.includes(".")) return q;
  return q.replace(/0+$/, "").replace(/\.$/, "");
}

export function relAge(iso: string | null | undefined, nowMs = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const d = (nowMs - t) / 1000;
  if (d < 45) return "just now";
  if (d < 3600) return Math.round(d / 60) + "m ago";
  if (d < 86400) return Math.round(d / 3600) + "h ago";
  return Math.round(d / 86400) + "d ago";
}

export function hhmmss(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function guardAge(sec: number | null | undefined): string {
  if (sec == null) return "—";
  if (sec < 90) return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  return Math.round(sec / 3600) + "h";
}
```

`dashboard-ui/src/lib/spark.ts` (geometry only — `Number()` on equity is allowed here):

```ts
export function sparkPath(
  values: number[], w: number, h: number,
): { line: string; area: string } {
  if (!values.length) return { line: "", area: "" };
  const mn = Math.min(...values);
  const mx = Math.max(...values);
  const rng = mx - mn || 1;
  const pad = h * 0.15;
  const n = values.length;
  const pts = values.map((v, i) => [
    n === 1 ? w : (i / (n - 1)) * w,
    h - pad - ((v - mn) / rng) * (h - pad * 2),
  ]);
  const line = "M" + pts.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" L");
  return { line, area: `${line} L${w} ${h} L0 ${h} Z` };
}
```

`dashboard-ui/src/lib/colors.ts` (kinds are the REAL journal kinds from `ops/dashboard/events_view.py`, not the design mock's):

```ts
const KIND_GROUPS: Record<string, string> = {
  fill: "fill",
  research_position_opened: "fill",
  research_position_closed: "fill",
  stop_hit: "order",
  order_rejected: "order",
  analysis_decision: "signal",
  research_vetting_run: "batch",
  research_drain_run: "batch",
  baseline_screen_run: "batch",
  daily_cycle_run: "batch",
  daily_cycle_completed: "batch",
  falsifier_tripped: "error",
  daily_halt: "error",
  kill_switch: "error",
  stop_failed: "error",
  startup_halted: "error",
  inconsistency: "error",
  guardian_check_error: "error",
  heartbeat_error: "error",
  research_escalation: "memo",
  resolution_due: "memo",
  catalyst_due: "memo",
  service_started: "muted",
  service_stopping: "muted",
};

export const kindClass = (kind: string) => "k-" + (KIND_GROUPS[kind] ?? "muted");

const SIDES = new Set(["buy", "sell", "short", "cover"]);
export const sideClass = (side: string) => {
  const s = side.toLowerCase();
  return "side-" + (SIDES.has(s) ? s : "other");
};
```

`dashboard-ui/src/lib/alerts.ts`:

```ts
import type { Health } from "../data/types";

export interface AlertView { tag: "ALERT" | "NOTICE"; conditions: string[] }

export function deriveAlert(health: Health | null): AlertView | null {
  if (!health) return null;
  const alerts: string[] = [];
  if (health.verdict === "STOPPED") alerts.push("service STOPPED — not trading");
  if (health.halts.daily_halt_today) alerts.push("daily drawdown halt in effect");
  if (health.halts.kill_switch_this_week) alerts.push("weekly kill-switch tripped");
  const notices: string[] = [];
  if (health.verdict === "STALE") notices.push("guardian heartbeat is stale");
  if (health.research_paused) notices.push("research paused");
  if (alerts.length) return { tag: "ALERT", conditions: [...alerts, ...notices] };
  if (notices.length) return { tag: "NOTICE", conditions: notices };
  return null;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm test && npx tsc --noEmit` — Expected: all PASS, clean typecheck.

- [ ] **Step 5: Commit**

```bash
git add dashboard-ui/src/lib/
git commit -m "feat(dashboard-ui): pure display mappers — money strings, pct, ages, spark, kind colors, alert derivation

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Theme stylesheet + HeaderBar + banners

**Files:**
- Replace: `dashboard-ui/src/app.css` (the complete theme — later tasks add NO css files; every class they use is defined here)
- Create: `dashboard-ui/src/components/HeaderBar.tsx`, `dashboard-ui/src/components/Banners.tsx`, `dashboard-ui/src/components/Unavail.tsx`

**Interfaces:**
- Consumes: `Health`, `Market`, `Section`, `isErr` (Task 3); `guardAge`, `hhmmss` (Task 4); `deriveAlert` (Task 4)
- Produces:
  - `<HeaderBar health={Section<Health>|null} market={Section<Market>|null} lastGoodAt={number|null} />`
  - `<DisconnectedBanner lastGoodAt={number|null} />`, `<AlertBanner health={Health|null} />` (from `Banners.tsx`)
  - `<Unavail msg={string} />` — the shared amber UNAVAIL chip every panel uses for `{"error"}` sections

- [ ] **Step 1: Write the full theme `dashboard-ui/src/app.css`**

Values are transcribed from `dashboard-ui/design/ops-dashboard.dc.html`; if a value here ever disagrees with the design file, the design file wins.

```css
:root {
  --bg:#0c0f14; --panel:#141922; --panel2:#1a212c; --elev:#212a37;
  --bd:#232c39; --bd2:#303c4c;
  --tx:#e8ecf3; --tx2:#93a0b2; --tx3:#5c6878;
  --acc:#39d3c2; --accd:#0f2b2b;
  --pos:#3fb27f; --neg:#e5645f; --amber:#d6a441; --sev:#e5645f;
  --sans:'IBM Plex Sans',system-ui,sans-serif;
  --mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,monospace;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--tx);
  font-family: var(--sans); font-size: 13px; line-height: 1.4;
  -webkit-font-smoothing: antialiased;
}
::-webkit-scrollbar { width: 9px; height: 9px; }
::-webkit-scrollbar-thumb { background: #2a333f; border-radius: 6px; }
::-webkit-scrollbar-track { background: transparent; }
a { color: var(--acc); text-decoration: none; }
a:hover { color: #5fe0d2; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes slidein { from { transform: translateX(30px); opacity: 0; } to { transform: translateX(0); opacity: 1; } }
@keyframes drop { from { transform: translateY(-8px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

.mono { font-family: var(--mono); }
.pos { color: var(--pos); } .neg { color: var(--neg); } .flat { color: var(--tx2); }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; flex: none; }
.dot.sm { width: 7px; height: 7px; }
.dot.pulse { animation: pulse 1.1s infinite; }
.dot.slow-pulse { width: 6px; height: 6px; animation: pulse 2s infinite; }
.d-pos { background: var(--pos); box-shadow: 0 0 10px rgba(63,178,127,.5); }
.d-amber { background: var(--amber); box-shadow: 0 0 10px rgba(214,164,65,.5); }
.d-sev { background: var(--sev); box-shadow: 0 0 10px rgba(229,100,95,.5); }

/* ---- header ---- */
.hdr {
  position: sticky; top: 0; z-index: 50;
  display: flex; align-items: center; justify-content: space-between; gap: 20px;
  padding: 13px 22px; background: rgba(12,15,20,.86);
  backdrop-filter: blur(12px); border-bottom: 1px solid var(--bd);
}
.hdr-left { display: flex; align-items: center; gap: 18px; min-width: 0; }
.hdr-right { display: flex; align-items: center; gap: 16px; }
.hdr-verdict { display: flex; align-items: center; gap: 9px; font-weight: 600; font-size: 13.5px; letter-spacing: .02em; }
.hdr-sep { width: 1px; height: 20px; background: var(--bd2); }
.hdr-kv { display: flex; align-items: center; gap: 6px; color: var(--tx2); font-size: 12px; }
.hdr-kv .k { color: var(--tx3); }
.hdr-kv .v { font-family: var(--mono); color: var(--tx); font-weight: 500; }
.hdr-kv .v.warn { color: var(--amber); }
.hdr-kv .v.bad { color: var(--sev); }
.market-chip {
  display: flex; align-items: center; gap: 8px; padding: 5px 12px;
  background: var(--panel); border: 1px solid var(--bd); border-radius: 8px;
  font-weight: 600; font-size: 12px;
}
.market-chip .sub { color: var(--tx3); font-family: var(--mono); font-size: 11px; font-weight: 400; }
.updated { display: flex; align-items: center; gap: 7px; color: var(--tx3); font-size: 11.5px; font-family: var(--mono); }
.updated .dot { background: var(--acc); }

/* ---- banners ---- */
.disc-banner {
  position: sticky; top: 0; z-index: 60;
  background: linear-gradient(90deg,#3a1414,#2a1010); border-bottom: 1px solid #5a2222;
  color: #f4b4b0; font-size: 12.5px; font-weight: 500; padding: 9px 22px;
  display: flex; align-items: center; gap: 10px; animation: drop .2s ease;
}
.disc-banner .when { color: #c98884; font-family: var(--mono); font-size: 11.5px; }
.alert-banner {
  margin: 14px 22px 0; border-radius: 10px; padding: 13px 16px;
  display: flex; gap: 14px; align-items: flex-start; animation: drop .2s ease;
}
.alert-banner.alert { border: 1px solid #5a2626; background: linear-gradient(90deg,rgba(58,20,20,.6),rgba(30,16,16,.4)); }
.alert-banner.notice { border: 1px solid #4a3a1c; background: linear-gradient(90deg,rgba(48,38,16,.5),rgba(28,24,14,.35)); }
.alert-tag { flex: none; font-family: var(--mono); font-size: 11px; font-weight: 700; letter-spacing: .06em; padding: 4px 9px; border-radius: 6px; }
.alert-banner.alert .alert-tag { color: #fff; background: var(--sev); }
.alert-banner.notice .alert-tag { color: #2a2013; background: var(--amber); }
.alert-conds { display: flex; flex-direction: column; gap: 5px; }
.alert-cond { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.alert-cond::before { content: ""; width: 4px; height: 4px; border-radius: 50%; background: currentColor; opacity: .7; }
.alert-banner.alert .alert-cond { color: #f0a9a5; }
.alert-banner.notice .alert-cond { color: #e6c583; }

/* ---- layout ---- */
.wrap { padding: 16px 22px 60px; max-width: 1600px; margin: 0 auto; }
.sec-head { display: flex; align-items: baseline; justify-content: space-between; margin: 0 2px 10px; }
.sec-head .t { font-size: 11px; font-weight: 600; letter-spacing: .09em; text-transform: uppercase; color: var(--tx3); }
.sec-head .r { font-size: 11px; color: var(--tx3); font-family: var(--mono); }
.cols { display: grid; grid-template-columns: 1.55fr 1fr; gap: 16px; align-items: start; }
.col { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
@media (max-width: 1100px) { .cols { grid-template-columns: 1fr; } }

/* ---- panels ---- */
.panel { background: var(--panel); border: 1px solid var(--bd); border-radius: 12px; overflow: hidden; }
.panel-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 13px 16px; border-bottom: 1px solid var(--bd);
  font-weight: 600; font-size: 13px;
}
.panel-head .r { font-size: 11px; color: var(--tx3); font-family: var(--mono); font-weight: 400; }
.panel-empty { padding: 22px 16px; color: var(--tx3); font-size: 12px; text-align: center; font-style: italic; }

/* ---- sleeve cards ---- */
.sleeves { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 18px; }
.card {
  cursor: pointer; background: var(--panel); border: 1px solid var(--bd);
  border-radius: 11px; padding: 14px 15px 12px; position: relative; overflow: hidden;
  transition: border-color .15s, transform .15s; text-align: left; color: inherit; font: inherit;
}
.card:hover { border-color: var(--bd2); transform: translateY(-2px); }
.card-top { display: flex; align-items: center; justify-content: space-between; margin-bottom: 9px; }
.card-top .nm { font-weight: 600; font-size: 13px; text-transform: capitalize; }
.card-top .day { font-family: var(--mono); font-size: 11px; font-weight: 500; }
.card-eq { font-family: var(--mono); font-size: 21px; font-weight: 600; letter-spacing: -.01em; }
.card svg { display: block; margin: 8px 0 9px; }
.card-foot { display: flex; align-items: center; justify-content: space-between; font-family: var(--mono); font-size: 11px; color: var(--tx3); }

/* ---- UNAVAIL chip ---- */
.unavail { display: flex; align-items: center; gap: 7px; padding: 10px 0 4px; color: var(--tx3); font-size: 11.5px; min-width: 0; }
.unavail .tag { font-family: var(--mono); font-size: 10px; font-weight: 600; color: var(--amber); background: rgba(214,164,65,.12); padding: 2px 6px; border-radius: 4px; flex: none; }
.unavail .msg { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.panel .unavail { padding: 12px 16px; }

/* ---- tables ---- */
table.tbl { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }
.tbl thead tr { color: var(--tx3); font-size: 10px; text-transform: uppercase; letter-spacing: .05em; }
.tbl th { text-align: left; font-weight: 500; padding: 4px 8px 6px 0; }
.tbl th.num, .tbl td.num { text-align: right; }
.tbl td { padding: 6px 8px 6px 0; color: var(--tx2); }
.tbl tbody tr { border-top: 1px solid var(--bd); }
.tbl td.sym { color: var(--tx); font-weight: 500; }
.tbl.padded th:first-child, .tbl.padded td:first-child { padding-left: 16px; }
.tbl.padded th:last-child, .tbl.padded td:last-child { padding-right: 16px; }
.tbl thead.sticky tr { position: sticky; top: 0; background: var(--panel); }

/* ---- accordion rows (positions, logs) ---- */
.acc-row { border-bottom: 1px solid var(--bd); }
.acc-row:last-child { border-bottom: none; }
.acc-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 11px 16px; cursor: pointer; width: 100%;
  background: none; border: none; color: inherit; font: inherit; text-align: left;
}
.acc-head:hover { background: var(--panel2); }
.acc-head .l { display: flex; align-items: center; gap: 9px; }
.acc-head .nm { font-weight: 500; text-transform: capitalize; }
.acc-head .sum { font-size: 11.5px; color: var(--tx2); font-family: var(--mono); }
.caret { color: var(--tx3); font-family: var(--mono); font-size: 11px; transition: transform .15s; display: inline-block; }
.caret.open { transform: rotate(90deg); }
.acc-body { padding: 0 16px 12px; }
.acc-none { color: var(--tx3); font-size: 12px; padding: 4px 0 8px; font-style: italic; }

/* ---- pills ---- */
.pill { font-family: var(--mono); font-size: 11px; padding: 3px 9px; border-radius: 6px; background: var(--panel2); border: 1px solid var(--bd); color: var(--tx2); }
.pill b { font-weight: 600; }
.pill-row { display: flex; flex-wrap: wrap; gap: 7px; }
.mini-label { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--tx3); }

/* ---- side + kind badges ---- */
.badge { font-size: 10px; font-weight: 600; padding: 2px 6px; border-radius: 4px; font-family: var(--mono); text-transform: uppercase; }
.side-buy { color: var(--pos); background: rgba(63,178,127,.14); }
.side-sell { color: var(--neg); background: rgba(229,100,95,.14); }
.side-short { color: var(--amber); background: rgba(214,164,65,.14); }
.side-cover { color: var(--acc); background: rgba(57,211,194,.13); }
.side-other { color: var(--tx2); background: var(--panel2); }
.k-fill { color: var(--pos); background: rgba(63,178,127,.12); }
.k-signal { color: var(--acc); background: rgba(57,211,194,.12); }
.k-memo { color: #b48be0; background: rgba(180,139,224,.13); }
.k-order { color: #e0b06a; background: rgba(224,176,106,.12); }
.k-batch { color: #6aa0e0; background: rgba(106,160,224,.12); }
.k-error { color: var(--neg); background: rgba(229,100,95,.14); }
.k-muted { color: var(--tx3); background: rgba(92,104,120,.16); }

/* ---- activity feed ---- */
.feed { max-height: 420px; overflow: auto; }
.feed-row { display: flex; gap: 10px; padding: 8px 14px; border-bottom: 1px solid var(--bd2); align-items: baseline; }
.feed-row:hover { background: var(--panel2); }
.feed-row .t { flex: none; font-family: var(--mono); font-size: 10.5px; color: var(--tx3); width: 52px; }
.feed-row .kind { flex: none; font-size: 10px; font-weight: 600; padding: 1px 6px; border-radius: 4px; font-family: var(--mono); align-self: flex-start; margin-top: 1px; }
.feed-row .txt { flex: 1; font-size: 12px; color: var(--tx); min-width: 0; overflow-wrap: anywhere; }
.feed-row .age { flex: none; font-family: var(--mono); font-size: 10.5px; color: var(--tx3); }
select.filter {
  background: var(--panel2); color: var(--tx2); border: 1px solid var(--bd);
  border-radius: 7px; font-size: 11px; font-family: var(--mono);
  padding: 4px 8px; cursor: pointer; outline: none;
}

/* ---- overnight ---- */
.kv-rows { display: flex; flex-direction: column; gap: 9px; font-size: 12px; padding: 14px 16px; }
.kv { display: flex; justify-content: space-between; align-items: baseline; }
.kv .k { color: var(--tx3); }
.kv .v { font-family: var(--mono); color: var(--tx2); }
.kv .v .sub { color: var(--tx3); }
.tag-active { font-size: 10px; font-family: var(--mono); font-weight: 600; color: var(--acc); background: var(--accd); padding: 3px 8px; border-radius: 5px; }
.tag-paused { font-size: 10px; font-family: var(--mono); font-weight: 700; color: var(--amber); background: rgba(214,164,65,.14); padding: 3px 8px; border-radius: 5px; }

/* ---- logs ---- */
.log-pre {
  margin: 0; padding: 12px 16px; background: #0a0d11; font-family: var(--mono);
  font-size: 11px; line-height: 1.6; color: var(--tx2); max-height: 220px;
  overflow: auto; white-space: pre-wrap; word-break: break-word;
}
.log-pre.err { color: #c88f8b; }
.btn-ghost {
  font-size: 10.5px; font-family: var(--mono); color: var(--acc);
  border: 1px solid var(--bd2); background: none; padding: 3px 9px;
  border-radius: 6px; cursor: pointer;
}
.btn-ghost:hover { background: var(--accd); }

/* ---- drill drawer ---- */
.overlay { position: fixed; inset: 0; z-index: 80; background: rgba(6,8,11,.6); backdrop-filter: blur(2px); border: none; }
.drawer {
  position: fixed; top: 0; right: 0; bottom: 0; z-index: 81;
  width: 480px; max-width: 92vw; background: var(--panel);
  border-left: 1px solid var(--bd2); box-shadow: -20px 0 50px rgba(0,0,0,.5);
  display: flex; flex-direction: column; animation: slidein .22s ease;
}
.drawer-head { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--bd); }
.drawer-head .nm { font-weight: 600; font-size: 16px; text-transform: capitalize; }
.drawer-head .kind { font-size: 11px; color: var(--tx3); font-family: var(--mono); margin-left: 10px; }
.drawer-x { cursor: pointer; color: var(--tx3); font-size: 20px; line-height: 1; padding: 4px 8px; border-radius: 6px; background: none; border: none; }
.drawer-x:hover { background: var(--panel2); color: var(--tx); }
.drawer-body { flex: 1; overflow: auto; padding: 20px; }
.drawer-eq { display: flex; align-items: baseline; gap: 14px; margin-bottom: 4px; }
.drawer-eq .big { font-family: var(--mono); font-size: 30px; font-weight: 600; letter-spacing: -.01em; }
.drawer-eq .day { font-family: var(--mono); font-size: 14px; font-weight: 600; }
.drawer-sub { display: flex; gap: 18px; font-family: var(--mono); font-size: 12px; color: var(--tx3); margin-bottom: 16px; }
.drawer svg.big { display: block; background: var(--panel2); border: 1px solid var(--bd); border-radius: 9px; margin-bottom: 20px; }
.drawer .mini-label { display: block; margin-bottom: 8px; }
.drawer .none { color: var(--tx3); font-size: 12px; font-style: italic; margin-bottom: 20px; }
```

- [ ] **Step 2: Write `Unavail.tsx`**

```tsx
export default function Unavail({ msg }: { msg: string }) {
  return (
    <div className="unavail">
      <span className="tag">UNAVAIL</span>
      <span className="msg" title={msg}>{msg}</span>
    </div>
  );
}
```

- [ ] **Step 3: Write `HeaderBar.tsx`**

```tsx
import type { Health, Market, Section } from "../data/types";
import { isErr } from "../data/types";
import { guardAge, hhmmss } from "../lib/format";

const VERDICT_DOT: Record<string, string> = {
  RUNNING: "d-pos", STALE: "d-amber", STOPPED: "d-sev", UNKNOWN: "d-sev",
};

export default function HeaderBar(props: {
  health: Section<Health> | null;
  market: Section<Market> | null;
  lastGoodAt: number | null;
}) {
  const h = props.health && !isErr(props.health) ? props.health : null;
  const m = props.market && !isErr(props.market) ? props.market : null;
  const verdict = h?.verdict ?? "UNKNOWN";
  const gAge = h?.guardian.age_seconds ?? null;
  const guardCls = verdict === "STOPPED" ? "bad" : verdict === "STALE" ? "warn" : "";
  return (
    <div className="hdr">
      <div className="hdr-left">
        <span className="hdr-verdict">
          <span className={`dot ${VERDICT_DOT[verdict]}`} />
          {verdict}
        </span>
        <span className="hdr-sep" />
        <span className="hdr-kv"><span className="k">broker</span>
          <span className="v">{h?.broker_mode ?? "—"}</span></span>
        <span className="hdr-kv"><span className="k">guardian</span>
          <span className={`v ${guardCls}`}>{guardAge(gAge)}</span></span>
      </div>
      <div className="hdr-right">
        <span className="market-chip">
          <span className={`dot sm ${m?.is_open ? "d-pos" : ""}`}
            style={m?.is_open ? undefined : { background: "var(--tx3)" }} />
          market {m ? (m.is_open ? "OPEN" : "CLOSED") : "—"}
          <span className="sub">
            {m?.is_open
              ? m.previous_close ? `prev close ${hhmmss(m.previous_close).slice(0, 5)}` : ""
              : m?.next_open ? `opens ${hhmmss(m.next_open).slice(0, 5)}` : ""}
          </span>
        </span>
        <span className="updated">
          <span className="dot slow-pulse" />
          updated {props.lastGoodAt ? hhmmss(new Date(props.lastGoodAt).toISOString()) : "—"}
        </span>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write `Banners.tsx`**

```tsx
import type { Health } from "../data/types";
import { deriveAlert } from "../lib/alerts";
import { hhmmss } from "../lib/format";

export function DisconnectedBanner({ lastGoodAt }: { lastGoodAt: number | null }) {
  return (
    <div className="disc-banner">
      <span className="dot sm pulse" style={{ background: "var(--sev)" }} />
      <span>dashboard disconnected</span>
      <span className="when">
        — last update {lastGoodAt ? hhmmss(new Date(lastGoodAt).toISOString()) : "never"}
      </span>
    </div>
  );
}

export function AlertBanner({ health }: { health: Health | null }) {
  const alert = deriveAlert(health);
  if (!alert) return null;
  return (
    <div className={`alert-banner ${alert.tag === "ALERT" ? "alert" : "notice"}`}>
      <span className="alert-tag">{alert.tag}</span>
      <div className="alert-conds">
        {alert.conditions.map((c) => (
          <div key={c} className="alert-cond">{c}</div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Verify it compiles and renders**

Run: `npx tsc --noEmit && npm test` — Expected: clean, tests still pass.
Optional eyeball: temporarily render `<HeaderBar health={null} market={null} lastGoodAt={null} />` from the placeholder `App.tsx` and `npm run dev` (proxy needs the live dashboard at :8321) — do not commit that temporary wiring.

- [ ] **Step 6: Commit**

```bash
git add dashboard-ui/src/app.css dashboard-ui/src/components/
git commit -m "feat(dashboard-ui): theme stylesheet, header bar, alert/disconnected banners

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Sparkline + sleeve cards

**Files:**
- Create: `dashboard-ui/src/components/Sparkline.tsx`, `dashboard-ui/src/components/SleeveCards.tsx`

**Interfaces:**
- Consumes: `Sleeve`, `Section`, `isErr`, `SLEEVE_ORDER` (Task 3); `fmtMoney`, `fmtPct` (Task 4); `sparkPath` (Task 4); `Unavail` (Task 5)
- Produces:
  - `<Sparkline series={SeriesPoint[]} w={number} h={number} up={boolean} className?={string} />`
  - `<SleeveCards sleeves={Section<Record<string, Section<Sleeve>>> | null} onOpen={(name: string) => void} />` — renders the section header ("Sleeves" + total) AND the card grid
  - exported helper `totalEquityLabel(sleeves): string` (used only here; sums via `Number` for the header label ONLY — a display convenience explicitly allowed by the spec, marked with a comment)

- [ ] **Step 1: Write `Sparkline.tsx`**

```tsx
import type { SeriesPoint } from "../data/types";
import { sparkPath } from "../lib/spark";

export default function Sparkline(props: {
  series: SeriesPoint[]; w: number; h: number; up: boolean; className?: string;
}) {
  const { w, h, up } = props;
  // Number() here is plotting geometry, not money display.
  const values = props.series.map((p) => Number(p.equity)).filter(Number.isFinite);
  const { line, area } = sparkPath(values, w, h);
  const stroke = up ? "var(--pos)" : "var(--neg)";
  const fill = up ? "rgba(63,178,127,.10)" : "rgba(229,100,95,.10)";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h}
      preserveAspectRatio="none" className={props.className} aria-hidden>
      <path d={area} fill={fill} />
      <path d={line} fill="none" stroke={stroke} strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
```

- [ ] **Step 2: Write `SleeveCards.tsx`**

```tsx
import type { Section, Sleeve } from "../data/types";
import { SLEEVE_ORDER, isErr } from "../data/types";
import { fmtMoney, fmtPct } from "../lib/format";
import Sparkline from "./Sparkline";
import Unavail from "./Unavail";

type SleevesSection = Section<Record<string, Section<Sleeve>>> | null;

// Header label only: floats never touch a displayed per-sleeve money value.
export function totalEquityLabel(sleeves: SleevesSection): string {
  if (!sleeves || isErr(sleeves)) return "—";
  let total = 0;
  let any = false;
  for (const s of Object.values(sleeves)) {
    if (!isErr(s) && s.equity != null) { total += Number(s.equity); any = true; }
  }
  return any ? fmtMoney(total.toFixed(2), 2) : "—";
}

function Card({ name, sleeve, onOpen }: {
  name: string; sleeve: Section<Sleeve>; onOpen: () => void;
}) {
  if (isErr(sleeve)) {
    return (
      <button type="button" className="card" onClick={onOpen}>
        <div className="card-top"><span className="nm">{name}</span></div>
        <Unavail msg={sleeve.error} />
      </button>
    );
  }
  const day = fmtPct(sleeve.day_pnl_pct);
  const life = fmtPct(sleeve.lifetime_pnl_pct);
  const up = day.cls !== "neg";
  return (
    <button type="button" className="card" onClick={onOpen}>
      <div className="card-top">
        <span className="nm">{name}</span>
        <span className={`day ${day.cls}`}>{day.text}</span>
      </div>
      <div className="card-eq" title={sleeve.equity ? `$${sleeve.equity}` : undefined}>
        {fmtMoney(sleeve.equity, 2)}
      </div>
      <Sparkline series={sleeve.series} w={120} h={30} up={up} />
      <div className="card-foot">
        <span>life <span className={life.cls}>{life.text}</span></span>
        <span>cash {fmtMoney(sleeve.cash, 0)}</span>
      </div>
    </button>
  );
}

export default function SleeveCards({ sleeves, onOpen }: {
  sleeves: SleevesSection; onOpen: (name: string) => void;
}) {
  // TS can't narrow `sleeves?.[name]` on the union — narrow once here.
  const data = sleeves && !isErr(sleeves) ? sleeves : null;
  return (
    <>
      <div className="sec-head">
        <span className="t">Sleeves</span>
        <span className="r">total {totalEquityLabel(sleeves)}</span>
      </div>
      {sleeves && isErr(sleeves) ? (
        <div className="panel" style={{ marginBottom: 18 }}><Unavail msg={sleeves.error} /></div>
      ) : (
        <div className="sleeves">
          {SLEEVE_ORDER.map((name) => {
            const s = data?.[name];
            if (!s) return null;
            return <Card key={name} name={name} sleeve={s} onOpen={() => onOpen(name)} />;
          })}
        </div>
      )}
    </>
  );
}
```

- [ ] **Step 3: Verify**

Run: `npx tsc --noEmit && npm test` — Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add dashboard-ui/src/components/Sparkline.tsx dashboard-ui/src/components/SleeveCards.tsx
git commit -m "feat(dashboard-ui): sleeve cards with sparklines

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Positions accordion + fills-today table

**Files:**
- Create: `dashboard-ui/src/components/PositionsPanel.tsx`, `dashboard-ui/src/components/FillsPanel.tsx`

**Interfaces:**
- Consumes: `Sleeve`, `Position`, `Fill`, `Section`, `isErr`, `SLEEVE_ORDER` (Task 3); `fmtQty`, `hhmmss` (Task 4); `sideClass` (Task 4); `Unavail` (Task 5)
- Produces: `<PositionsPanel sleeves={…} />`, `<FillsPanel sleeves={…} />` — both take the same `Section<Record<string, Section<Sleeve>>> | null`

- [ ] **Step 1: Write `PositionsPanel.tsx`**

```tsx
import { useState } from "react";
import type { Section, Sleeve } from "../data/types";
import { SLEEVE_ORDER, isErr } from "../data/types";
import { fmtQty } from "../lib/format";
import Unavail from "./Unavail";

type SleevesSection = Section<Record<string, Section<Sleeve>>> | null;

function Group({ name, sleeve, open, onToggle }: {
  name: string; sleeve: Section<Sleeve>; open: boolean; onToggle: () => void;
}) {
  const err = isErr(sleeve);
  const rows = err ? [] : sleeve.positions;
  const summary = err ? "unavailable"
    : `${rows.length} position${rows.length === 1 ? "" : "s"}`;
  return (
    <div className="acc-row">
      <button type="button" className="acc-head" onClick={onToggle}>
        <span className="l">
          <span className={`caret ${open ? "open" : ""}`}>▸</span>
          <span className="nm">{name}</span>
        </span>
        <span className="sum">{summary}</span>
      </button>
      {open && (
        <div className="acc-body">
          {err ? <Unavail msg={sleeve.error} />
            : rows.length === 0 ? <div className="acc-none">no open positions</div> : (
            <table className="tbl">
              <thead><tr>
                <th>symbol</th><th className="num">qty</th>
                <th className="num">entry</th><th className="num">stop</th>
              </tr></thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.symbol}>
                    <td className="sym">{r.symbol}</td>
                    <td className={`num ${r.quantity.startsWith("-") ? "neg" : ""}`}>
                      {fmtQty(r.quantity)}
                    </td>
                    <td className="num">{r.entry ?? "—"}</td>
                    <td className="num" style={{ color: "var(--tx3)" }}>{r.stop ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

export default function PositionsPanel({ sleeves }: { sleeves: SleevesSection }) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({ momentum: true });
  const err = sleeves && isErr(sleeves) ? sleeves : null;
  // TS can't narrow `sleeves?.[name]` on the union — narrow once here.
  const data = sleeves && !isErr(sleeves) ? sleeves : null;
  const total = !data ? 0
    : Object.values(data).reduce(
        (n, s) => n + (isErr(s) ? 0 : s.positions.length), 0);
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Positions</span>
        <span className="r">{total} open</span>
      </div>
      {err ? <Unavail msg={err.error} /> : (
        SLEEVE_ORDER.map((name) => {
          const s = data?.[name];
          if (!s) return null;
          return (
            <Group key={name} name={name} sleeve={s} open={!!expanded[name]}
              onToggle={() => setExpanded((e) => ({ ...e, [name]: !e[name] }))} />
          );
        })
      )}
    </div>
  );
}
```

- [ ] **Step 2: Write `FillsPanel.tsx`**

```tsx
import type { Fill, Section, Sleeve } from "../data/types";
import { SLEEVE_ORDER, isErr } from "../data/types";
import { fmtQty, hhmmss } from "../lib/format";
import { sideClass } from "../lib/colors";
import Unavail from "./Unavail";

type SleevesSection = Section<Record<string, Section<Sleeve>>> | null;

export default function FillsPanel({ sleeves }: { sleeves: SleevesSection }) {
  const err = sleeves && isErr(sleeves) ? sleeves : null;
  // TS can't narrow `sleeves[name]` on the union — narrow once here.
  const data = sleeves && !isErr(sleeves) ? sleeves : null;
  const fills: (Fill & { sleeve: string })[] = [];
  if (data) {
    for (const name of SLEEVE_ORDER) {
      const s = data[name];
      if (s && !isErr(s)) for (const f of s.fills_today) fills.push({ ...f, sleeve: name });
    }
    fills.sort((a, b) => a.filled_at.localeCompare(b.filled_at));
  }
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Fills today</span>
        <span className="r">{fills.length}</span>
      </div>
      {err ? <Unavail msg={err.error} />
        : fills.length === 0 ? <div className="panel-empty">no fills today</div> : (
        <div style={{ maxHeight: 260, overflow: "auto" }}>
          <table className="tbl padded">
            <thead className="sticky"><tr>
              <th>time</th><th>sleeve</th><th>side</th><th>symbol</th>
              <th className="num">qty</th><th className="num">price</th>
            </tr></thead>
            <tbody>
              {fills.map((f, i) => (
                <tr key={`${f.sleeve}-${f.filled_at}-${i}`}>
                  <td>{hhmmss(f.filled_at).slice(0, 5)}</td>
                  <td style={{ fontFamily: "var(--sans)", textTransform: "capitalize" }}>{f.sleeve}</td>
                  <td><span className={`badge ${sideClass(f.side)}`}>{f.side.toUpperCase()}</span></td>
                  <td className="sym">{f.symbol}</td>
                  <td className="num">{fmtQty(f.quantity)}</td>
                  <td className="num" style={{ color: "var(--tx)" }}>{f.price}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Verify** — `npx tsc --noEmit && npm test` — Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add dashboard-ui/src/components/PositionsPanel.tsx dashboard-ui/src/components/FillsPanel.tsx
git commit -m "feat(dashboard-ui): positions accordion and merged fills-today table

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: Research funnel, overnight, anomalies panels

**Files:**
- Create: `dashboard-ui/src/components/FunnelPanel.tsx`, `dashboard-ui/src/components/OvernightPanel.tsx`, `dashboard-ui/src/components/AnomaliesPanel.tsx`

**Interfaces:**
- Consumes: `Funnel`, `AnomalyEntry`, `Section`, `isErr` (Task 3); `relAge`, `hhmmss` (Task 4); `Unavail` (Task 5)
- Produces: `<FunnelPanel funnel={Section<Funnel>|null} />`, `<OvernightPanel funnel={Section<Funnel>|null} />`, `<AnomaliesPanel anomalies={Section<Record<string, AnomalyEntry>>|null} />`

- [ ] **Step 1: Write `FunnelPanel.tsx`**

```tsx
import type { Funnel, Section } from "../data/types";
import { isErr } from "../data/types";
import { relAge } from "../lib/format";
import Unavail from "./Unavail";

const MEMO_PILL_ORDER = ["open", "closed", "rejected"];
const MEMO_COLORS: Record<string, string> = {
  open: "var(--acc)", closed: "var(--pos)", rejected: "var(--tx3)",
};
const SIGNAL_LABELS: Record<string, string> = {
  falsifier_tripped: "falsifier", research_escalation: "escalation",
  resolution_due: "resolution due", catalyst_due: "catalyst due",
};

function tierBadge(tier: number | string): { label: string; color: string } {
  const n = String(tier).replace(/^t/i, "");
  const color = n === "1" ? "var(--acc)" : n === "2" ? "var(--tx2)" : "var(--tx3)";
  return { label: `T${n}`, color };
}

export default function FunnelPanel({ funnel }: { funnel: Section<Funnel> | null }) {
  const body = () => {
    if (!funnel) return <div className="panel-empty">waiting for snapshot…</div>;
    if (isErr(funnel)) return <Unavail msg={funnel.error} />;
    const run = funnel.screener.last_run;
    const byStatus = funnel.memos.by_status;
    const pillKeys = [
      ...MEMO_PILL_ORDER.filter((k) => k in byStatus),
      ...Object.keys(byStatus).filter((k) => !MEMO_PILL_ORDER.includes(k)).sort(),
    ];
    return (
      <div style={{ padding: "14px 16px", display: "flex", flexDirection: "column", gap: 13 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--tx2)" }}>
          <span style={{ color: "var(--tx3)" }}>screener last run</span>
          <span className="mono" style={{ color: "var(--tx)" }}>
            {run ? `${run.passed_count} / ${run.universe_size} · ${run.asof}` : "—"}
          </span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <span className="mini-label">memos</span>
          <div className="pill-row">
            {pillKeys.length === 0 && <span className="pill">none</span>}
            {pillKeys.map((k) => (
              <span key={k} className="pill">
                <b style={{ color: MEMO_COLORS[k] ?? "var(--tx2)" }}>{byStatus[k]}</b> {k}
              </span>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
          <span className="mini-label">signals · 7d</span>
          <div className="pill-row">
            {Object.entries(funnel.signals_7d).map(([k, n]) => (
              <span key={k} className="pill">
                <b style={{ color: n > 0 ? "var(--amber)" : "var(--tx3)" }}>{n}</b>{" "}
                {SIGNAL_LABELS[k] ?? k}
              </span>
            ))}
          </div>
        </div>
        <div style={{ borderTop: "1px solid var(--bd)", paddingTop: 11 }}>
          <span className="mini-label">open memos</span>
          {funnel.memos.open.length === 0
            ? <div className="acc-none">none open</div> : (
            <table className="tbl" style={{ marginTop: 8, fontFamily: "var(--sans)" }}>
              <tbody>
                {funnel.memos.open.map((m) => {
                  const tier = tierBadge(m.conviction_tier);
                  return (
                    <tr key={m.memo_id}>
                      <td className="sym mono">{m.ticker}</td>
                      <td>{m.thesis_type}</td>
                      <td className="num mono" style={{ color: tier.color, fontWeight: 600, fontSize: 10 }}>
                        {tier.label}
                      </td>
                      <td className="num" style={{ color: "var(--tx3)", fontSize: 11 }}>
                        {relAge(m.created_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    );
  };
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Research funnel</span>
        <span className="r">screen → memo → trade</span>
      </div>
      {body()}
    </div>
  );
}
```

- [ ] **Step 2: Write `OvernightPanel.tsx`**

```tsx
import type { Funnel, Section } from "../data/types";
import { isErr } from "../data/types";
import { hhmmss, relAge } from "../lib/format";
import Unavail from "./Unavail";

// The overnight research window runs 00:00–08:00 local — same convention
// the overnight services themselves use.
export const inOvernightWindow = (d = new Date()) => d.getHours() < 8;

export default function OvernightPanel({ funnel }: { funnel: Section<Funnel> | null }) {
  const o = funnel && !isErr(funnel) ? funnel.overnight : null;
  return (
    <div className="panel">
      <div className="panel-head">
        <span>Overnight</span>
        {o?.paused ? <span className="tag-paused">◼ PAUSED</span>
          : inOvernightWindow() ? <span className="tag-active">WINDOW ACTIVE</span> : null}
      </div>
      {funnel && isErr(funnel) ? <Unavail msg={funnel.error} /> : (
        <div className="kv-rows">
          <div className="kv">
            <span className="k">last vetting</span>
            <span className="v">
              {o?.last_vetting_run ? hhmmss(o.last_vetting_run.at) : "—"}{" "}
              <span className="sub">· {relAge(o?.last_vetting_run?.at)}</span>
            </span>
          </div>
          <div className="kv">
            <span className="k">last drain</span>
            <span className="v">
              {o?.last_drain_run ? hhmmss(o.last_drain_run.at) : "—"}{" "}
              <span className="sub">· {relAge(o?.last_drain_run?.at)}</span>
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Write `AnomaliesPanel.tsx`**

```tsx
import type { AnomalyEntry, Section } from "../data/types";
import { isErr } from "../data/types";
import { relAge } from "../lib/format";
import Unavail from "./Unavail";

export default function AnomaliesPanel({ anomalies }: {
  anomalies: Section<Record<string, AnomalyEntry>> | null;
}) {
  const body = () => {
    if (!anomalies) return <div className="panel-empty">waiting for snapshot…</div>;
    if (isErr(anomalies)) return <Unavail msg={anomalies.error} />;
    const rows = Object.entries(anomalies)
      .filter(([, v]) => v.count > 0)
      .sort(([, a], [, b]) => b.count - a.count);
    if (rows.length === 0) return <div className="panel-empty">none in last 7 days</div>;
    return (
      <table className="tbl padded">
        <tbody>
          {rows.map(([kind, v]) => (
            <tr key={kind}>
              <td>{kind}</td>
              <td className="num" style={{ color: v.count > 2 ? "var(--amber)" : "var(--tx2)", fontWeight: 600 }}>
                {v.count}
              </td>
              <td className="num" style={{ color: "var(--tx3)", fontSize: 11 }}>
                {relAge(v.last_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  };
  return (
    <div className="panel">
      <div className="panel-head"><span>Anomalies</span><span className="r">7d</span></div>
      {body()}
    </div>
  );
}
```

- [ ] **Step 4: Verify** — `npx tsc --noEmit && npm test` — Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add dashboard-ui/src/components/FunnelPanel.tsx dashboard-ui/src/components/OvernightPanel.tsx dashboard-ui/src/components/AnomaliesPanel.tsx
git commit -m "feat(dashboard-ui): research funnel, overnight, anomalies panels

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Activity feed + logs panel

**Files:**
- Create: `dashboard-ui/src/components/ActivityFeed.tsx`, `dashboard-ui/src/components/LogsPanel.tsx`

**Interfaces:**
- Consumes: `EventItem` (Task 3); `fetchLog` (Task 3); `hhmmss`, `relAge` (Task 4); `kindClass` (Task 4)
- Produces: `<ActivityFeed events={EventItem[]} />`, `<LogsPanel />` (self-contained; fetches lazily)

- [ ] **Step 1: Write `ActivityFeed.tsx`**

```tsx
import { useMemo, useState } from "react";
import type { EventItem } from "../data/types";
import { kindClass } from "../lib/colors";
import { hhmmss, relAge } from "../lib/format";

export default function ActivityFeed({ events }: { events: EventItem[] }) {
  const [filter, setFilter] = useState("all");
  const kinds = useMemo(
    () => [...new Set(events.map((e) => e.kind))].sort(), [events]);
  const shown = filter === "all" ? events : events.filter((e) => e.kind === filter);
  return (
    <div className="panel">
      <div className="panel-head" style={{ padding: "11px 14px" }}>
        <span>Activity</span>
        <select className="filter" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="all">all kinds</option>
          {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
      </div>
      <div className="feed">
        {shown.length === 0 && <div className="panel-empty">no events</div>}
        {shown.map((e) => (
          <div key={`${e.source}-${e.id}`} className="feed-row">
            <span className="t">{hhmmss(e.at)}</span>
            <span className={`kind ${kindClass(e.kind)}`}>{e.kind}</span>
            <span className="txt">{e.text}</span>
            <span className="age">{relAge(e.at)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Write `LogsPanel.tsx`**

```tsx
import { useState } from "react";
import { fetchLog } from "../data/api";

function LogSection({ file }: { file: "out" | "err" }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);

  const load = async () => {
    try {
      const r = await fetchLog(file);
      setText(r.text || "(empty)");
    } catch (e) {
      setText(`failed to load: ${e instanceof Error ? e.message : e}`);
    }
  };

  const toggle = () => {
    const opening = !open;
    setOpen(opening);
    if (opening && text === null) void load();
  };

  return (
    <div className="acc-row">
      <button type="button" className="acc-head" onClick={toggle}>
        <span className="l">
          <span className={`caret ${open ? "open" : ""}`}>▸</span>
          <span className="mono" style={{ fontSize: 12, color: "var(--tx2)" }}>
            ops.{file}.log
          </span>
        </span>
        {open && (
          <span className="btn-ghost" role="button"
            onClick={(e) => { e.stopPropagation(); void load(); }}>
            refresh
          </span>
        )}
      </button>
      {open && <pre className={`log-pre ${file === "err" ? "err" : ""}`}>{text ?? "loading…"}</pre>}
    </div>
  );
}

export default function LogsPanel() {
  return (
    <div className="panel">
      <div className="panel-head"><span>Logs</span></div>
      <LogSection file="out" />
      <LogSection file="err" />
    </div>
  );
}
```

- [ ] **Step 3: Verify** — `npx tsc --noEmit && npm test` — Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add dashboard-ui/src/components/ActivityFeed.tsx dashboard-ui/src/components/LogsPanel.tsx
git commit -m "feat(dashboard-ui): activity feed with kind filter, lazy log tails

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Drill-down drawer + App assembly

**Files:**
- Create: `dashboard-ui/src/components/SleeveDrillDrawer.tsx`
- Replace: `dashboard-ui/src/App.tsx`

**Interfaces:**
- Consumes: everything above.
- Produces: the complete page. `App` owns `drill: string | null` state; card click opens the drawer, overlay click / ✕ / Escape closes it.

- [ ] **Step 1: Write `SleeveDrillDrawer.tsx`**

```tsx
import { useEffect } from "react";
import type { Section, Sleeve } from "../data/types";
import { isErr } from "../data/types";
import { fmtMoney, fmtPct, fmtQty, hhmmss } from "../lib/format";
import { sideClass } from "../lib/colors";
import Sparkline from "./Sparkline";
import Unavail from "./Unavail";

const KIND_LABELS: Record<string, string> = {
  momentum: "intraday momentum",
  research: "LLM long theses",
  baseline: "passive benchmark",
  short: "short-selling",
  insider: "Form-4 clusters",
};

export default function SleeveDrillDrawer({ name, sleeve, onClose }: {
  name: string; sleeve: Section<Sleeve> | undefined; onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const body = () => {
    if (!sleeve) return <div className="panel-empty">no data</div>;
    if (isErr(sleeve)) return <Unavail msg={sleeve.error} />;
    const day = fmtPct(sleeve.day_pnl_pct);
    const life = fmtPct(sleeve.lifetime_pnl_pct);
    return (
      <>
        <div className="drawer-eq">
          <span className="big" title={sleeve.equity ? `$${sleeve.equity}` : undefined}>
            {fmtMoney(sleeve.equity, 2)}
          </span>
          <span className={`day ${day.cls}`}>{day.text}</span>
        </div>
        <div className="drawer-sub">
          <span>lifetime <span className={life.cls}>{life.text}</span></span>
          <span>cash <span style={{ color: "var(--tx2)" }}>{fmtMoney(sleeve.cash, 2)}</span></span>
        </div>
        <Sparkline series={sleeve.series} w={520} h={120}
          up={day.cls !== "neg"} className="big" />

        <span className="mini-label">Positions</span>
        {sleeve.positions.length === 0
          ? <div className="none">no open positions</div> : (
          <table className="tbl" style={{ marginBottom: 20 }}>
            <thead><tr>
              <th>symbol</th><th className="num">qty</th>
              <th className="num">entry</th><th className="num">stop</th>
            </tr></thead>
            <tbody>
              {sleeve.positions.map((p) => (
                <tr key={p.symbol}>
                  <td className="sym">{p.symbol}</td>
                  <td className={`num ${p.quantity.startsWith("-") ? "neg" : ""}`}>{fmtQty(p.quantity)}</td>
                  <td className="num">{p.entry ?? "—"}</td>
                  <td className="num" style={{ color: "var(--tx3)" }}>{p.stop ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <span className="mini-label">Fills today</span>
        {sleeve.fills_today.length === 0
          ? <div className="none">no fills today</div> : (
          <table className="tbl">
            <tbody>
              {sleeve.fills_today.map((f, i) => (
                <tr key={`${f.filled_at}-${i}`}>
                  <td style={{ color: "var(--tx3)" }}>{hhmmss(f.filled_at).slice(0, 5)}</td>
                  <td><span className={`badge ${sideClass(f.side)}`}>{f.side.toUpperCase()}</span></td>
                  <td className="sym">{f.symbol}</td>
                  <td className="num">{fmtQty(f.quantity)}</td>
                  <td className="num" style={{ color: "var(--tx)" }}>{f.price}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </>
    );
  };

  return (
    <>
      <button type="button" className="overlay" onClick={onClose} aria-label="close" />
      <div className="drawer" role="dialog" aria-label={`${name} sleeve detail`}>
        <div className="drawer-head">
          <span>
            <span className="nm">{name}</span>
            <span className="kind">{KIND_LABELS[name] ?? ""}</span>
          </span>
          <button type="button" className="drawer-x" onClick={onClose}>✕</button>
        </div>
        <div className="drawer-body">{body()}</div>
      </div>
    </>
  );
}
```

- [ ] **Step 2: Replace `App.tsx`**

```tsx
import { useState } from "react";
import { isDisconnected, usePoll } from "./data/poll";
import { isErr } from "./data/types";
import ActivityFeed from "./components/ActivityFeed";
import AnomaliesPanel from "./components/AnomaliesPanel";
import { AlertBanner, DisconnectedBanner } from "./components/Banners";
import FillsPanel from "./components/FillsPanel";
import FunnelPanel from "./components/FunnelPanel";
import HeaderBar from "./components/HeaderBar";
import LogsPanel from "./components/LogsPanel";
import OvernightPanel from "./components/OvernightPanel";
import PositionsPanel from "./components/PositionsPanel";
import SleeveCards from "./components/SleeveCards";
import SleeveDrillDrawer from "./components/SleeveDrillDrawer";

export default function App() {
  const poll = usePoll();
  const [drill, setDrill] = useState<string | null>(null);
  const snap = poll.snapshot;
  const health = snap && !isErr(snap.health) ? snap.health : null;
  const sleeves = snap?.sleeves ?? null;
  const drillSleeve = drill && sleeves && !isErr(sleeves) ? sleeves[drill] : undefined;

  return (
    <>
      {isDisconnected(poll) && <DisconnectedBanner lastGoodAt={poll.lastGoodAt} />}
      <HeaderBar health={snap?.health ?? null} market={snap?.market ?? null}
        lastGoodAt={poll.lastGoodAt} />
      <AlertBanner health={health} />
      <div className="wrap">
        <SleeveCards sleeves={sleeves} onOpen={setDrill} />
        <div className="cols">
          <div className="col">
            <PositionsPanel sleeves={sleeves} />
            <FillsPanel sleeves={sleeves} />
            <FunnelPanel funnel={snap?.funnel ?? null} />
          </div>
          <div className="col">
            <ActivityFeed events={poll.events} />
            <OvernightPanel funnel={snap?.funnel ?? null} />
            <AnomaliesPanel anomalies={snap?.anomalies_7d ?? null} />
            <LogsPanel />
          </div>
        </div>
      </div>
      {drill && (
        <SleeveDrillDrawer name={drill} sleeve={drillSleeve}
          onClose={() => setDrill(null)} />
      )}
    </>
  );
}
```

- [ ] **Step 3: Verify against the live API**

Run: `npx tsc --noEmit && npm test` — Expected: clean.
Then `npm run dev` and open the printed URL (the live dashboard service on :8321 feeds the proxy): header shows RUNNING + real guardian age, five sleeve cards with real equity, positions/fills/funnel/activity populated, drawer opens and closes (click, ✕, Escape). Kill the dev proxy target reachability (e.g. stop nothing — instead temporarily set the proxy port to 9999 in vite.config.ts and reload) to see the disconnected banner appear after ~15s; revert before committing.

- [ ] **Step 4: Commit**

```bash
git add dashboard-ui/src/components/SleeveDrillDrawer.tsx dashboard-ui/src/App.tsx
git commit -m "feat(dashboard-ui): sleeve drill-down drawer and full app assembly

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 11: Cutover — build into static/, delete the old frontend

**Files:**
- Delete: `ops/dashboard/static/app.js`, `ops/dashboard/static/style.css` (old vanilla frontend)
- Replace: `ops/dashboard/static/index.html` (+ new `ops/dashboard/static/assets/app.js`, `assets/app.css`) — all build output
- Test: existing `tests/ops/dashboard/test_server.py` static tests

- [ ] **Step 1: Build**

Run (from `dashboard-ui/`): `npm run build`
Expected: `emptyOutDir` removes the old `app.js`/`style.css`; static/ now holds `index.html` and `assets/app.js`, `assets/app.css`.

- [ ] **Step 2: Server tests still pass**

Run: `python -m pytest tests/ops/dashboard/ -v`
Expected: all PASS (`test_index_served`, traversal guard, etc. — they assert serving behavior, not the old file contents; if any test greps for old asset names, update it to the new names in this task).

- [ ] **Step 3: Serve the real thing**

Run: `python -m ops.cli dashboard --port 8399` (or however `ops/cli.py dashboard` is invoked — check `python -m ops.cli --help`; fall back to `python -c "from ops.dashboard.server import serve; serve(8399)"`).
Open `http://127.0.0.1:8399/` — the React dashboard renders from built assets with live data. Ctrl-C afterwards.

- [ ] **Step 4: Commit the cutover**

```bash
git add -A ops/dashboard/static/
git commit -m "feat(dashboard): React UI replaces vanilla frontend (built assets)

Built from dashboard-ui/ (npm run build). Old app.js/style.css removed.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 12: opsdash.test hostname (hosts entry + pf redirect + LaunchDaemon)

**Files:**
- Create: `ops/deploy/setup_opsdash.sh`, `ops/deploy/com.tradingagents.opsdash-pf.plist`
- Modify: `dashboard-ui/README.md` (access section)

**Interfaces:**
- Produces: `sudo bash ops/deploy/setup_opsdash.sh` → `http://opsdash.test` works locally, survives reboot. Idempotent; re-running is safe.

- [ ] **Step 1: Write `ops/deploy/com.tradingagents.opsdash-pf.plist`**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.tradingagents.opsdash-pf</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/sh</string>
    <string>-c</string>
    <string>/sbin/pfctl -E; /sbin/pfctl -a "com.apple/250.opsdash" -f /etc/pf.anchors/com.tradingagents.opsdash</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
```

- [ ] **Step 2: Write `ops/deploy/setup_opsdash.sh`**

```bash
#!/bin/bash
# One-time, idempotent setup for http://opsdash.test -> 127.0.0.1:8321.
#
# Three pieces:
#   1. /etc/hosts maps opsdash.test to 127.0.0.1 (IPv4 only, on purpose:
#      the pf rule below is inet-only, and an ::1 mapping would send
#      browsers to an unredirected IPv6 port 80 first).
#   2. A pf rdr anchor redirects loopback :80 -> :8321. It is loaded into
#      the "com.apple/*" anchor namespace because macOS's stock
#      /etc/pf.conf evaluates `rdr-anchor "com.apple/*"` — this way we
#      never edit /etc/pf.conf (which OS updates can clobber).
#   3. A LaunchDaemon reloads the anchor at every boot.
#
# The dashboard server itself still binds 127.0.0.1:8321 only; nothing
# becomes reachable from the network.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "run with sudo: sudo bash $0" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
ANCHOR_FILE=/etc/pf.anchors/com.tradingagents.opsdash
ANCHOR_NAME="com.apple/250.opsdash"
PLIST_SRC="$REPO_DIR/com.tradingagents.opsdash-pf.plist"
PLIST_DST=/Library/LaunchDaemons/com.tradingagents.opsdash-pf.plist

# 1. hosts entry
if ! grep -qE '^[^#]*\bopsdash\.test\b' /etc/hosts; then
  printf '127.0.0.1\topsdash.test\n' >> /etc/hosts
  echo "added opsdash.test to /etc/hosts"
else
  echo "/etc/hosts already maps opsdash.test"
fi

# 2. pf anchor
cat > "$ANCHOR_FILE" <<'EOF'
rdr pass on lo0 inet proto tcp from any to 127.0.0.1 port 80 -> 127.0.0.1 port 8321
EOF
/sbin/pfctl -E 2>/dev/null || true
/sbin/pfctl -a "$ANCHOR_NAME" -f "$ANCHOR_FILE"
echo "pf anchor loaded ($ANCHOR_NAME)"

# 3. LaunchDaemon for boot persistence
install -m 644 -o root -g wheel "$PLIST_SRC" "$PLIST_DST"
launchctl bootout system "$PLIST_DST" 2>/dev/null || true
launchctl bootstrap system "$PLIST_DST"
echo "LaunchDaemon installed"

echo "ok — open http://opsdash.test"
```

- [ ] **Step 3: Lint both files**

Run: `bash -n ops/deploy/setup_opsdash.sh && plutil -lint ops/deploy/com.tradingagents.opsdash-pf.plist`
Expected: `OK` from plutil, no output from bash -n. If `shellcheck` is installed, run it too and fix findings.

- [ ] **Step 4: Document access in `dashboard-ui/README.md`** — append:

```markdown
## Access via http://opsdash.test

One-time setup (adds a hosts entry, a pf loopback redirect :80→:8321, and a
boot-persistent LaunchDaemon; see comments in the script):

    sudo bash ops/deploy/setup_opsdash.sh

Uninstall: remove the `opsdash.test` line from /etc/hosts, then
`sudo launchctl bootout system /Library/LaunchDaemons/com.tradingagents.opsdash-pf.plist`
and delete that plist plus /etc/pf.anchors/com.tradingagents.opsdash.
```

- [ ] **Step 5: Live verification (requires the user — sudo is interactive)**

Ask the user to run: `! sudo bash ops/deploy/setup_opsdash.sh`
Then verify: `curl -s http://opsdash.test/api/snapshot | head -c 100` returns JSON, and `curl -s --connect-timeout 2 http://$(ipconfig getifaddr en0)/ ; echo "exit=$?"` from the same machine fails to connect on port 80 via the LAN address (redirect is loopback-only).

- [ ] **Step 6: Commit**

```bash
git add ops/deploy/setup_opsdash.sh ops/deploy/com.tradingagents.opsdash-pf.plist dashboard-ui/README.md
git commit -m "feat(deploy): opsdash.test hostname — hosts entry + pf loopback redirect + boot persistence

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 13: Final verification + branch finish

- [ ] **Step 1: Full test sweep**

Run: `python -m pytest tests/ops/dashboard/ -v` and (from `dashboard-ui/`) `npm test && npx tsc --noEmit`
Expected: all PASS.

- [ ] **Step 2: /verify the end-to-end flow**

Invoke the `verify` skill: drive the built dashboard against the live service (open `http://opsdash.test` if Task 12's sudo step ran, else `http://127.0.0.1:8321`), confirm live data in every panel, drawer open/close, log tails load, activity filter works.

- [ ] **Step 3: Finish the branch**

Invoke `superpowers:finishing-a-development-branch` — expected outcome: PR from `feat/opsdash-react-ui` to `main`. After merge, deployment to the live checkout (`TradingAgents-live`) is: pull, then gracefully restart the dashboard service (never `kickstart -k`).
