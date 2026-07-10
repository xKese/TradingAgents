# IBKR Portfolio Context Design

## Goal

Make `batch-analyze` aware of the user's actual live Interactive Brokers portfolio so its transaction and risk recommendations reflect existing quantities, cost basis, cash, portfolio weights, and concentration. After the ticker analyses finish, produce one coordinated portfolio-level review.

This feature is read-only and specific to one live IBKR account connected through Trader Workstation (TWS). It does not place, modify, or cancel orders.

## Scope

The first version will:

- Connect to live TWS at `127.0.0.1:7496` using `ib_async`.
- Require TWS API read-only mode.
- Load one account's balances and stock positions once at batch start.
- Add the frozen account snapshot to each ticker graph state.
- Expose portfolio context only to the Trader, three risk analysts, and Portfolio Manager.
- Keep market, news, sentiment, and fundamentals research independent of portfolio ownership.
- Run one post-batch portfolio review over all successful ticker decisions.
- Save a sanitized snapshot and portfolio-review artifacts with the batch reports.

The first version will not include order submission, order modification, broker abstraction, background monitoring, historical portfolio storage, or unattended TWS authentication. Open-order retrieval is also excluded because strict TWS read-only mode may not expose it consistently.

## CLI Interface

The existing command remains valid and unchanged without portfolio context. Portfolio awareness is enabled with:

```powershell
python -m cli.main batch-analyze `
  --tickers AAOI,CCXI,DRAM,ENHA,HIMS,IREN,NOK,NUAI,ONDS,OUST,PENG,RKLB `
  --ibkr-context `
  --analysts all `
  --depth shallow `
  --provider openrouter `
  --model deepseek/deepseek-v4-flash
```

Options:

- `--ibkr-context`: load the TWS snapshot and enable the final portfolio review.
- `--ibkr-host`: default `127.0.0.1`.
- `--ibkr-port`: default `7496` for live TWS.
- `--ibkr-client-id`: configurable dedicated client ID with a documented safe default.

Environment-variable equivalents may be provided for scheduled runs, including `TRADINGAGENTS_IBKR_CLIENT_ID`. Explicit CLI values take precedence.

## Components

### IBKR snapshot loader

Add a focused IBKR module responsible for:

1. Connecting to TWS with a bounded timeout.
2. Confirming exactly one managed account is available.
3. Reading account NAV, cash, gross position value, buying power or available funds, base currency, and stock positions.
4. Reading each position's symbol, quantity, average cost, market price, market value, currency, and unrealized P&L when available.
5. Calculating portfolio weight and unrealized return when inputs permit.
6. Disconnecting after the snapshot is complete.
7. Rendering a concise context block for agents.

The loader returns a plain serializable snapshot rather than leaking live `ib_async` objects into graph state or saved files.

### Graph state

Add `portfolio_context` to `AgentState` and initial-state construction. It contains the frozen account snapshot plus ticker-specific ownership context.

`TradingAgentsGraph.propagate` accepts optional portfolio context. Existing callers that omit it preserve current behavior.

### Decision-stage prompts

The Trader, aggressive risk analyst, conservative risk analyst, neutral risk analyst, and Portfolio Manager receive portfolio context. Prompts require them to:

- Recognize whether the ticker is already owned.
- Distinguish `Hold existing`, `Add`, `Trim`, `Exit`, and `Avoid`.
- Reconcile proposed sizing with current shares and portfolio weight.
- Express suggested changes in whole shares when practical.
- Consider available cash and concentration.
- Avoid blindly anchoring decisions to average cost.
- State when missing data prevents a share-level recommendation.
- Never interpret an incomplete account fetch as zero ownership.

Research agents do not receive ownership data.

### Post-batch portfolio reviewer

After all ticker runs complete, one additional LLM call receives:

- The sanitized account snapshot.
- Each successful ticker's final rating, target, horizon, and proposed action.
- Current position weights and cash.
- A soft concentration warning threshold of 10% of NAV.

It produces:

- `portfolio_review.md`: executive assessment, prioritized actions, conflicts, risk triggers, and data-quality warnings.
- `portfolio_actions.csv`: ticker, current shares, proposed shares or share change, current weight, proposed weight when calculable, action, priority, and rationale summary.

The reviewer must identify conflicts such as a standalone Buy recommendation for a position that is already the portfolio's largest holding. Its output remains advisory and is never passed to an execution API.

## Saved Data and Privacy

The batch directory will include `portfolio_snapshot.json`. It may contain balances and position data required to reproduce the review, but must exclude account IDs, usernames, credentials, authentication tokens, and connection secrets.

All monetary values and currencies must be labelled. The code must not silently add values denominated in different currencies.

## Error Handling

Portfolio-aware analysis fails before making any LLM calls when:

- TWS is unavailable or the connection times out.
- API access is disabled or rejected.
- No managed account is returned.
- More than one account is returned.
- Position results are empty while account metrics indicate nonzero gross exposure.

Partial data rules:

- A position without a current price retains its quantity and cost information; unavailable weight or P&L fields are labelled as such.
- Currency conversion gaps remain explicit rather than being guessed.
- A ticker absent from a successfully completed position fetch is represented as `owned: false`.
- A ticker that cannot be matched confidently is marked uncertain, never assumed unowned.
- One failed ticker analysis does not prevent the portfolio reviewer from evaluating the successful reports, but the review lists the missing ticker as a coverage warning.

## Safety

- TWS is configured in API read-only mode.
- The application contains no order placement, modification, or cancellation path.
- No execution method is imported or exposed by the IBKR module.
- Connecting to live TWS does not authorize trading activity.
- Portfolio artifacts are recommendations for manual review only.

## Testing and Acceptance

Automated verification will include:

- Snapshot-loader unit tests with a fake IBKR client.
- Single-account and connection-failure tests.
- Tests that inconsistent empty positions fail safely.
- Tests for symbol matching, missing prices, mixed currencies, and snapshot sanitization.
- Graph-state and prompt tests proving only decision-stage agents receive portfolio context.
- Regression tests proving non-IBKR `analyze` and `batch-analyze` behavior is unchanged.
- Batch tests proving the TWS snapshot is loaded once and shared across ticker runs.
- Portfolio-review tests covering concentration conflicts and failed ticker coverage.

Live acceptance will be performed in the Miniconda `tradingagents` environment:

1. Start and authenticate live TWS.
2. Enable socket API access, API read-only mode, and port `7496`.
3. Run a snapshot-only smoke check and exit without invoking an LLM.
4. Compare NAV, cash, quantities, average costs, and market values with TWS.
5. Run one shallow portfolio batch with `--ibkr-context`.
6. Confirm the report treats existing positions as owned and flags concentration conflicts.
7. Confirm no orders were submitted, modified, or cancelled.

## Success Criteria

The feature succeeds when a live read-only batch run can correctly recognize all current holdings, prevent false statements such as "zero open position" for owned stocks, translate recommendations into account-aware whole-share proposals, and produce one coordinated portfolio review without exposing sensitive account identifiers or adding any trading capability.
