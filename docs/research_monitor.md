# Research Monitor Runbook (Phase C — the loop)

Phase C of docs/superpowers/specs/2026-07-06-finish-research-system-design.md.
Open memos are watched mechanically; humans get exceptions. No LLM runs in
the monitor — escalations queue re-research hits for `ops research run`.

## What runs when

| job | where | when | what |
|---|---|---|---|
| research_monitor | ops daemon (APScheduler) | 16:20 ET mon-fri | falsifiers, drawdown, catalysts, resolution-due |
| screen | launchd com.tradingagents.screen | Sat 10:00 | fills the pending queue; auto write-off of delisted baseline names |
| research run | launchd com.tradingagents.research | Sat 12:00 | drains the pending queue into memos |

Manual: `ops research monitor` (safe anywhere; empty stores are a no-op).

## What gets pushed

| event | urgency | trigger |
|---|---|---|
| falsifier_tripped | high | a machine-checkable falsifier held for its consecutive_periods |
| research_escalation | high | falsifier trip or drawdown <= -30% queued a re-research hit |
| resolution_due | normal | expected_holding_months elapsed (memo exit checklist in the body) |
| catalyst_due | normal | a hard-dated event-sleeve catalyst date passed |

Re-notification is deduped per memo/falsifier over a 7-day window (journal
count_events — no side state). Escalations dedupe naturally: a symbol with a
hit already pending is not re-queued.

## Falsifier metrics evaluable today

drawdown_from_cost_pct (split-era-corrected vs entry_price_ref),
gross_margin_pct, revenue_yoy_pct, net_debt_to_ebitda. Anything else is
"unevaluable" — counted in the research_monitor_run summary event, never a
silent pass. consecutive_periods = trading days for price metrics, fiscal
years for fundamental ones.

## Requirements in the daemon environment

Fundamental falsifier checks need SEC_EDGAR_USER_AGENT in the ops daemon
plist env (re-render gotcha: install-service resets the env block — re-merge
creds per RUNBOOK). Without it, price checks still run; fundamental checks
degrade to unevaluable with a note in the run summary.

## Delisted baseline names

Each weekly screen probes every held baseline position once; a quote failure
journals baseline_quote_failure. Three consecutive failing runs write the
position off at the last buy-fill price (baseline_auto_writeoff, surfaced in
the screen's --notify summary). Manual override remains:
`ops research write-off SYMBOL --price P`.

## Inspecting

    sqlite3 ${XDG_STATE_HOME:-~/.local/state}/tradingagents/ops_journal.sqlite \
      "SELECT at, kind, payload FROM events WHERE kind LIKE 'research_%' OR kind LIKE '%falsifier%' ORDER BY id DESC LIMIT 20"
