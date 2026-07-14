# Insider-Cluster Sleeve — Runbook

The fifth paper sleeve: mechanically buys small/mid-caps where several
insiders just bought with their own cash. Own journal
(`insider_journal.sqlite`), memo store (`insider_memos.sqlite`), and Form 4
signal store (`insider_signals.sqlite`); $10k bucket
(`OPS_INSIDER_STARTING_CASH`). Design:
`docs/superpowers/specs/2026-07-13-short-and-insider-sleeves-design.md`.

Glossary: a *cluster buy* = ≥2 distinct insiders making open-market
purchases (Form 4 code P) NOT under a 10b5-1 plan (pre-scheduled trades
carry no information) within 30 days. The LLM never gates a trade here —
this sleeve is the control arm measuring what the research funnel's LLM
stages actually add over a raw, well-documented signal.

## What runs when

| job | where | when | what |
|---|---|---|---|
| insider_scan | ops daemon | 00:15 daily | EDGAR daily-index Form 4 scan → signal store; one index fetch/day + one doc fetch per in-universe Form 4; 404 = holiday, not error |
| insider_trade | ops daemon | 16:29 ET mon-fri | mechanical exits then cluster entries; pushes `insider_trade_run` |
| insider memo pass | inside the overnight tick | last, after research + short stages | one cheap structured call per un-memoed entry; ds4 only spins if the queue is non-empty |

## Signal rules (`ops/insider/clusters.py`)

- window 30 days; ≥ 2 distinct insiders with ≥ $10k of qualified buys each
- **STRONG** if ≥ 3 buyers, OR aggregate ≥ $250k, OR CEO/CFO participated;
  else **BASIC**
- 90-day per-name cooldown after an entry

## Entries and exits (`ops/insider/trading.py`)

| rule | value |
|---|---|
| BASIC / STRONG slice | 3% / 5% of sleeve equity |
| name cap | 5% at cost |
| max positions | 25 |
| ADV cap | 5% of 20-day dollar ADV |
| min order | $100 |
| deny-list | `config.deny_list` |
| **stop** | −20% from entry |
| **target** | +40% from entry |
| **time stop** | 126 calendar days (~90 trading) |

Exits run first, first match wins (stop → target → time). No discretion
anywhere; the memo is never consulted.

## Memo-lite (`ops/insider/memo_lite.py`)

Each entry gets a compact memo the following night (thesis_type `event`,
event_type `insider_cluster`, status `open`, `vetting=None`), with Form 4
accessions as evidence and the mechanical stop restated as a
machine-checkable falsifier. Authoring failure leaves the entry queued —
the trade always stands. At exit the memo resolves mechanically: realized
return vs IWM (Russell 2000 ETF) over the same window; target →
`thesis_right_made_money`, stop → `thesis_wrong_lost_money`, time-stop by
sign of the return.

## Manual commands

```bash
# signal store peek
sqlite3 ~/.local/state/tradingagents/insider_signals.sqlite \
  "SELECT symbol, insider_name, transaction_date, shares, price FROM insider_transactions ORDER BY transaction_date DESC LIMIT 10;"
# entries + memo queue
sqlite3 ~/.local/state/tradingagents/insider_signals.sqlite \
  "SELECT symbol, asof, memo_id FROM sleeve_entries ORDER BY asof DESC LIMIT 10;"
```
