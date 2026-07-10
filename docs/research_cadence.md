# Research Cadence Runbook — every-3-days screen + nightly drain

How the research sleeve's screen and deep-research (memo) queue get worked
through, unattended, inside the always-on `ops run` service. Companion to
[`docs/research_pipelines.md`](research_pipelines.md) (which pipeline does
what) and [`docs/research_screener.md`](research_screener.md) /
[`docs/research_monitor.md`](research_monitor.md) (what each stage does).

## What runs when

| job | where | when | what |
|---|---|---|---|
| `research_overnight` | ops daemon (APScheduler), job id `research_overnight` | 00:00 America/New_York, nightly | screens if due, then deadline-boxed drain of the pending queue into memos |
| `ops research kick` | manual CLI | on demand | one-shot screen (ignoring the gate) -> drain-all -> trade, for an end-to-end run |

`research_overnight` is `_research_overnight_tick` in `ops/main.py`, registered
in `_start_full_scheduler` via `CronTrigger(hour=0, minute=0)` on the
`America/New_York`-timezoned scheduler, `max_instances=1`,
`misfire_grace_time=600`. It is only registered when a `config` is passed to
the full scheduler (same guard as the other research/overview jobs) — not in
the degraded guardian-only scheduler.

## Step 1: the 3-day screen gate

Before touching ds4, the tick asks `ScreenStore.last_run()` for the most
recent `screen_runs` row and reads its `created_at`. The screen re-runs
(`ops.research.run.run_screen`) only if:

- no screen has ever run (`last is None`), or
- `_days_since_iso(last["created_at"]) >= config.research_screen_interval_days`
  (default **3** days).

If the gate says "not due," the tick skips straight to draining whatever is
already queued — the nightly job always attempts a drain, even on nights it
doesn't screen.

`ops screen` (the standalone CLI command) and `ops research kick` both call
`run_screen` directly and are **not** subject to this gate — they always run
the screen when invoked.

## Step 2: the deadline-boxed drain

After the (possible) screen, the tick brings up the managed ds4 backend
(`build_managed_backend(load_managed_backend_config())`, `ensure_up()` /
`shutdown()` in a `finally`) and calls `ops.research.drain.drain_pending`,
which works through `ScreenStore.pending_hits()` one name at a time and stops
— checked before each name, so a name already in flight always finishes —
on the first of:

1. `should_stop()` is true (graceful daemon shutdown requested).
2. `now() >= deadline` — the local wall-clock has reached
   `research_drain_deadline_hour` (default **8**, i.e. 08:00
   America/New_York). Computed by `_drain_deadline()` in `ops/main.py` as
   *today's* local `HH:00`.
3. the pending queue is empty.

A `ResearchError` (configuration problem, e.g. bad model spec) aborts the
whole batch and is re-raised; any other per-name exception marks that hit
`failed` and the drain continues — one bad name must not strand the queue.

Each nightly run records a `research_drain_run` event (screened_this_run,
researched, failed, still_pending, hit_deadline) on success, or
`research_drain_error` on any exception — the tick never raises, so a bad
night can't wedge the scheduler.

## The "since last screened" TTL (`research_screen_ttl_days`)

Independent of the 3-day gate, `ScreenStore` will not re-queue a symbol that
was screened (any status: pending/researched/failed/expired) more recently
than `research_screen_ttl_days` (default **7**) days ago — checked in
`_screened_within()` and wired through both `record_run()`'s `ttl_days` and
`enqueue_hit()`'s `ttl_days` kwargs. `run_screen` passes
`config.research_screen_ttl_days` into `record_run`, so a fresh screen pass
silently skips re-queueing anything it already looked at within the window,
regardless of what happened to that earlier hit.

**Exception:** the monitor's escalation path (`ops/research/monitor.py`,
`screen_store.enqueue_hit(...)` on a falsifier trip or drawdown) calls
`enqueue_hit` with no `ttl_days` argument, i.e. the default `0`, which
disables the TTL check entirely. A name that just tripped a falsifier is
always re-queued for re-research even if it was screened yesterday.

## Env knobs

| Var | Config field | Default | Meaning |
|---|---|---|---|
| `OPS_RESEARCH_SCREEN_INTERVAL_DAYS` | `research_screen_interval_days` | `3` | minimum age (days) of the last screen run before the nightly tick re-screens |
| `OPS_RESEARCH_DRAIN_DEADLINE_HOUR` | `research_drain_deadline_hour` | `8` | local (America/New_York) hour-of-day the overnight drain must stop by; validated to `0..23` |
| `OPS_RESEARCH_SCREEN_TTL_DAYS` | `research_screen_ttl_days` | `7` | a symbol screened within this many days is not re-queued by a later screen pass (any status) |

## ds4 non-overlap invariant

The overnight drain (00:00-08:00 America/New_York, market closed) and the
momentum multi-agent graph's ticks (`orchestrator_tick`, 09:30-16:00
America/New_York, `mon-fri`) must never run ds4 at the same time — that's
why the drain deadline defaults to 08:00, well ahead of the 09:30 first
momentum tick. The overnight tick owns the managed ds4 backend for its own
duration only (`ensure_up`/`shutdown` in a `finally`) and both jobs run on
the same single-process `BackgroundScheduler`, which serializes job
execution. Do not lower `research_drain_deadline_hour` below the point where
this margin holds, and do not move the momentum tick earlier without
re-checking this margin.

## Manual end-to-end run: `ops research kick`

```
ops research kick
```

A one-shot demo/bootstrap command (`ops/cli.py`, `research_kick`):
screens now (ignoring the 3-day gate) -> drains the *entire* pending queue
(no deadline) -> runs `trade_research_sleeve` so paper positions appear in a
single manual invocation. Independent of the nightly schedule; useful to
seed the first memos + positions on a fresh deploy or to force an
out-of-cycle pass. It fails fast if `SEC_EDGAR_USER_AGENT` is unset, same as
the nightly tick.

## Retired: the two Saturday launchd plists

The screen and research-drain used to run as two separate Saturday launchd
jobs (`com.tradingagents.screen`, `com.tradingagents.research`), installed
via the now-removed `ops install-screen-service` / `ops install-research-service`
CLI commands. Both are folded into `research_overnight` inside the always-on
`ops run` service (`com.tradingagents.ops`) — there is nothing left for the
two plists to do.

**On the deployed machine**, unload and delete both:

```bash
launchctl unload ~/Library/LaunchAgents/com.tradingagents.screen.plist \
                  ~/Library/LaunchAgents/com.tradingagents.research.plist
rm ~/Library/LaunchAgents/com.tradingagents.screen.plist \
   ~/Library/LaunchAgents/com.tradingagents.research.plist
```

If they're left loaded, they will queue a stray screen/drain against the
same stores as the nightly job for no benefit (and, if launchd's clock ever
overlaps with a live drain, a stray ds4 contender).

## Inspecting

```bash
sqlite3 ${XDG_STATE_HOME:-~/.local/state}/tradingagents/ops_journal.sqlite \
  "SELECT at, kind, payload FROM events WHERE kind IN ('research_drain_run', 'research_drain_error') ORDER BY id DESC LIMIT 10"
```

Last screen run (age drives the 3-day gate):

```bash
sqlite3 ${XDG_STATE_HOME:-~/.local/state}/tradingagents/research_screen.sqlite \
  "SELECT run_id, asof, created_at, passed_count FROM screen_runs ORDER BY created_at DESC LIMIT 5"
```
