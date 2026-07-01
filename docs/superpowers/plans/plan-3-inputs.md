# Plan 3 Inputs — Must-fix items from the Plan 2 review

Plan 3 (Always-on service + `RobinhoodBroker` + notifications) is the third phased plan of the live-v1 spec. The Plan 2 whole-branch review surfaced two Important design gaps that were deferred from that branch on the explicit condition that Plan 3 addresses them **first**, before the always-on orchestrator is wired.

## Ticket #1 — Formalise the sell-all convention

**Where it lives today:** `ops/position_guardian.py` builds `Order(notional_dollars=Decimal("0"))` to close a whole position, and `ops/broker/paper.py::_fill_sell` treats `notional == 0` as "sell entire position." This is a **paper-broker-only convention** that the Robinhood MCP will not understand — it will either reject the zero-notional order outright or, worse, submit a $0 market order.

**What Plan 3 must do:**
- Make sell-all a first-class concept on the `Broker` ABC — either a new `close_position(symbol: str) -> Fill` method, or an explicit `sell_all: bool` field on `Order`. Design that reads well at both the paper and live layers.
- Update `PaperBroker` and `PositionGuardian` to use the new API.
- `RobinhoodBroker` implements it by looking up the current fractional-share quantity and placing a sized SELL.
- Add tests for both broker implementations.

**Why this is Plan-3-first:** the moment `RobinhoodBroker` exists, the current convention will silently misfire against real money. Better to design the API before wiring the live broker than to retrofit it after.

## Ticket #2 — Guardian must honour `Position.stop_loss_price`

**Where it lives today:** `ops/strategy/post_earnings_momentum.py` computes an entry-relative `stop_loss_price` per position and stores it on the resulting `Position` via `PaperBroker._fill_buy`. The `PositionGuardian.check_stops_once` in `ops/position_guardian.py:63-64` then **ignores it**, checking only the global `self._cfg.per_position_stop_pct`. The math happens to line up today because the strategy uses the same config value, but `Position.stop_loss_price` is effectively decorative.

**What Plan 3 must do:**
- Guardian reads `pos.stop_loss_price` when present, falls back to `cfg.per_position_stop_pct * entry` only when the position was opened outside a strategy that set a per-position stop.
- Add tests: a position with an explicit stop above the config default triggers earlier; a position with no stop uses the config default.

**Why this is Plan-3-first:** any future strategy that varies stops per position (ATR-based, volatility-scaled, tighter-around-earnings) would be silently overridden. Better to fix this while the guardian is single-threaded and easy to reason about than after Plan 3 wraps it in a background loop.

## Other Plan 3 risk flags (address in normal Plan 3 order, not "first")

Also from the review, non-blocking but worth remembering:

- **Guardian threading race:** `check_stops_once()` iterates a `get_positions()` snapshot unlocked, then places SELLs inside the broker lock. Under Plan 3's threading model, a strategy thread could top up a position between snapshot and SELL. Either wrap the whole loop in the broker lock, or re-read the position immediately before the SELL. Combined with Ticket #2 this compounds.
- **`TradingAgentsPipelineAdapter._ensure_graph`** is not thread-safe — needs a lock in Plan 3.
- **`start_of_day_equity` / `start_of_week_equity`** are constants passed from the Plan 2 CLI. Plan 3's orchestrator must derive them from journal-persisted equity snapshots, otherwise the drawdown rules silently become no-ops in the always-on loop.
- **Journal-based broker state recovery** (positions + cash rebuild from journal on orchestrator startup) needs a dedicated test.
- **`_fetch_from_wikipedia`** falls back to a hard `RuntimeError` if the page format changes AND the cache is stale. Nice-to-have: fall back to stale cache with a stderr warning.
