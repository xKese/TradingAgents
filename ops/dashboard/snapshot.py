"""Read-only snapshot builder for the local ops dashboard.

Same contract as ops/status.py::build_status, wider scope: reads ONLY the
sqlite stores (mode=ro URIs — a hard guarantee, not a convention) plus two
flag files. No broker, no MCP, no OAuth, no quotes, no LLM, no network.

Every top-level section is exception-isolated: a missing or mid-migration
store turns into {"error": ...} for that section while the rest of the
snapshot still builds — a partial dashboard beats a blank page.
"""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ops import events
from ops.config import OpsConfig
from ops.journal import Journal
from ops.live_gate import count_live_buy_fills, flip_epoch

# A guardian pass starts every 60s; 3 missed passes = stale. Matches the
# heartbeat's staleness window in ops/main.py.
GUARDIAN_STALE_S = 180.0


def jsonable(value: Any) -> Any:
    """Deep-convert to JSON-safe types. Decimal -> str (never float: this
    is money), aware datetime -> UTC ISO-8601."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def section(builder: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return jsonable(builder())
    except Exception as exc:  # noqa: BLE001 — isolation is the point
        return {"error": f"{type(exc).__name__}: {exc}"}


def ro_conn(path: str) -> sqlite3.Connection:
    """mode=ro sqlite connection (raises OperationalError if missing)."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _event_view(ev: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    if ev is None:
        return None
    return {
        "at": ev["at"],
        "age_seconds": (now - ev["at"]).total_seconds(),
        "payload": ev["payload"],
    }


def _health_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    with Journal(config.journal_path, readonly=True) as j:
        started = j.last_event(events.KIND_SERVICE_STARTED)
        stopping = j.last_event(events.KIND_SERVICE_STOPPING)
        halts = {
            "daily_halt_today": j.has_event_today(events.KIND_DAILY_HALT, now=now),
            "kill_switch_this_week": j.has_event_since_last_monday(
                events.KIND_KILL_SWITCH, now=now),
        }
        cycle_run = j.last_event(events.KIND_DAILY_CYCLE_RUN)
        cycle_done = j.last_event(events.KIND_DAILY_CYCLE_COMPLETED)
        cursor = j.get_cursor("notify")
        max_event_id = j.last_event_id_before(now) or 0
        epoch = flip_epoch(j)
        live_fills = count_live_buy_fills(j)
        heartbeat_errors = j.count_events(
            events.KIND_HEARTBEAT_ERROR, since=now - timedelta(hours=24))

    guardian_alive_at: datetime | None = None
    try:
        mtime = os.stat(config.guardian_liveness_path).st_mtime
        guardian_alive_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        pass
    guardian_age = (
        (now - guardian_alive_at).total_seconds()
        if guardian_alive_at is not None else None
    )

    # Verdict: journal start/stop ordering first (a fresh liveness file
    # from a guardian that outlived a clean shutdown must not say
    # RUNNING), then liveness recency.
    if started is None:
        verdict = "UNKNOWN"
    elif stopping is not None and stopping["at"] > started["at"]:
        verdict = "STOPPED"
    elif guardian_age is None:
        verdict = "UNKNOWN"
    elif guardian_age <= GUARDIAN_STALE_S:
        verdict = "RUNNING"
    else:
        verdict = "STALE"

    last_stopping = _event_view(stopping, now)
    if last_stopping is not None:
        last_stopping["exit_code"] = last_stopping.pop("payload").get("exit_code")
    last_started = _event_view(started, now)
    if last_started is not None:
        last_started.pop("payload")

    return {
        "verdict": verdict,
        "broker_mode": config.broker_mode,
        "last_started": last_started,
        "last_stopping": last_stopping,
        "guardian": {"alive_at": guardian_alive_at, "age_seconds": guardian_age},
        "daily_cycle": {
            "last_run_at": cycle_run["at"] if cycle_run else None,
            "last_completed_at": cycle_done["at"] if cycle_done else None,
        },
        "halts": halts,
        "research_paused": os.path.exists(config.research_pause_flag_path),
        "live_gate": {
            "flip_marker_present": epoch is not None,
            "flip_at": epoch,
            "live_buy_fills": live_fills,
            "cap": config.live_max_position,
            "gate_count": config.live_fill_gate_count,
            "remaining": max(0, config.live_fill_gate_count - live_fills),
        },
        "notify": {
            "cursor": cursor,
            "max_event_id": max_event_id,
            "lag": max(0, max_event_id - cursor),
        },
        "heartbeat_errors_24h": heartbeat_errors,
    }


def _market_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    from ops.scheduler.market_calendar import MarketCalendar

    cal = MarketCalendar()
    return {
        "is_open": cal.is_open_now(now),
        "next_open": cal.next_open(now),
        "previous_close": cal.previous_close(now),
        "is_trading_day": cal.is_trading_day(now.date()),
        "research_deadline_hour_et": config.research_drain_deadline_hour,
    }


def _sleeves_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    raise NotImplementedError("Task 4")


def _funnel_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    raise NotImplementedError("Task 5")


def _anomalies_section(config: OpsConfig, now: datetime) -> dict[str, Any]:
    raise NotImplementedError("Task 4")


def build_snapshot(
    config: OpsConfig, *, now: datetime | None = None,
) -> dict[str, Any]:
    when = now if now is not None else datetime.now(timezone.utc)
    return {
        "generated_at": when.isoformat(),
        "health": section(lambda: _health_section(config, when)),
        "sleeves": section(lambda: _sleeves_section(config, when)),
        "funnel": section(lambda: _funnel_section(config, when)),
        "anomalies_7d": section(lambda: _anomalies_section(config, when)),
        "market": section(lambda: _market_section(config, when)),
    }
