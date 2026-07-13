# Fork Extension Audit

This audit describes the state of `mehul532/TradingAgents` at upstream-derived
commit `01477f9` (`v0.3.1`) before the fork extensions in this change. The
repository remains an Apache-2.0-licensed extension of
`TauricResearch/TradingAgents`; existing copyright, license, attribution, and
Git history are not replaced.

The classifications below use these meanings:

- **Already implemented**: the requested behavior exists and should be reused.
- **Partially implemented**: useful building blocks exist, but the acceptance
  criteria are not met end to end.
- **Missing**: no repository implementation was found.
- **Implemented differently than expected**: the repository solves a related
  problem through a different mechanism that should remain intact.

## Architecture baseline

The upstream-derived workflow is a sequential LangGraph pipeline. Selected
analyst nodes are registered by `GraphSetup.setup_graph()` in
`tradingagents/graph/setup.py` from metadata produced by
`build_analyst_execution_plan()` in
`tradingagents/graph/analyst_execution.py`. Each tool-calling analyst loops
through its `ToolNode`, clears the shared message history, and hands off to the
next analyst. The bull/bear research debate, research manager, trader, three
risk debaters, and portfolio manager then run in sequence.

`AgentState` in `tradingagents/agents/utils/agent_states.py` carries prose
reports. `Propagator.create_initial_state()` in
`tradingagents/graph/propagation.py` initializes that state.
`TradingAgentsGraph._run_graph()` in
`tradingagents/graph/trading_graph.py` executes the workflow, persists a JSON
state snapshot, stores the final decision in `TradingMemoryLog`, and optionally
clears a successful checkpoint. `write_report_tree()` in
`tradingagents/reporting.py` writes the human-readable report tree.

## Feature classification

### 1. Operational Signals Analyst — **Missing**

Existing analyst factories cover market, sentiment, news, and fundamentals:

- `create_market_analyst()` in
  `tradingagents/agents/analysts/market_analyst.py`
- `create_sentiment_analyst()` in
  `tradingagents/agents/analysts/sentiment_analyst.py`
- `create_news_analyst()` in
  `tradingagents/agents/analysts/news_analyst.py`
- `create_fundamentals_analyst()` in
  `tradingagents/agents/analysts/fundamentals_analyst.py`

No analyst factory, schema, state field, tool category, execution-plan entry,
conditional router, CLI enum, report section, or test covers operational
signals such as backlog, bookings, capacity, concentration, bottlenecks, or
demand visibility. The extension should follow the existing factory and
metadata-driven graph conventions rather than add a parallel pipeline.

### 2. Claim-level evidence and citations — **Partially implemented**

Useful grounding mechanisms already exist:

- `build_verified_market_snapshot()` in
  `tradingagents/dataflows/market_data_validator.py` deterministically supplies
  point-in-time OHLCV and indicator values to the Market Analyst.
- `create_market_analyst()` explicitly makes that snapshot authoritative and
  requires conflicts to be surfaced.
- `_extract_article_data()` and `get_news_yfinance()` in
  `tradingagents/dataflows/yfinance_news.py` preserve publisher, title, link,
  and publication time in prose tool output.
- `build_instrument_context()` and `resolve_instrument_identity()` in
  `tradingagents/agents/utils/agent_utils.py` anchor every agent to a resolved
  ticker/company identity.
- `SentimentReport`, `ResearchPlan`, `TraderProposal`, and
  `PortfolioDecision` in `tradingagents/agents/schemas.py` provide typed output
  for selected agents, while `invoke_structured_or_freetext()` in
  `tradingagents/agents/utils/structured.py` preserves provider compatibility.

The requested citation contract is absent. There is no shared evidence object,
stable evidence/citation ID, claim-to-evidence relationship, machine-readable
evidence state, deterministic citation validator, source consolidation,
conflict representation, or generated Sources section. Existing uses of the
word “evidence” are prompt instructions, not structural guarantees.

### 3. Temporal integrity — **Partially implemented**

Several point-in-time controls are already tested and must remain:

- `load_ohlcv()` and `filter_financials_by_date()` in
  `tradingagents/dataflows/stockstats_utils.py` remove price rows and financial
  statement columns after the analysis date.
- `_verified_rows()` in
  `tradingagents/dataflows/market_data_validator.py` defensively applies the
  requested-date cutoff again.
- `_in_news_window()` in `tradingagents/dataflows/yfinance_news.py` rejects
  future-dated articles and treats undated historical news as unusable.
- `_filter_reports_by_date()` in
  `tradingagents/dataflows/alpha_vantage_fundamentals.py` removes financial
  reports with fiscal period endings after the requested date.
- Tests in `tests/test_news_lookahead.py`,
  `tests/test_market_data_validator.py`,
  `tests/test_yfinance_stale_ohlcv_guard.py`, and
  `tests/test_alpha_vantage_hardening.py` cover these controls.

The current safeguards do not prove when a financial statement became public:
fiscal period end is not filing/publication date. `get_fundamentals()` in both
`tradingagents/dataflows/y_finance.py` and
`tradingagents/dataflows/alpha_vantage_fundamentals.py` can return a current
company overview during a historical run. StockTwits and Reddit are fetched by
`create_sentiment_analyst()` without a historical snapshot contract. The
operational extension therefore needs explicit publication/filing-date
validation and must mark unverifiable live material unusable rather than infer
historical availability.

### 4. Evaluation harness — **Missing**

The repository has a substantial deterministic pytest suite configured in
`pyproject.toml` and `.github/workflows/ci.yml`, plus the paid-provider manual
smoke script `scripts/smoke_structured_output.py`. These are implementation
tests, not a research-quality evaluation package. There is no `evaluations`
package, fixture scenario dataset, baseline matrix, evaluator-provider
abstraction, deterministic metric report, or JSON/CSV/Markdown evaluation
output.

### 5. LLMOps tracing and observability — **Partially implemented**

- `StatsCallbackHandler` in `cli/stats_handler.py` counts LLM calls, tool calls,
  and input/output tokens when provider usage metadata is available.
- `TradingAgentsGraph` accepts LangChain callbacks and the CLI forwards them to
  both LLM constructors and graph execution.
- Debug execution in `TradingAgentsGraph._run_graph()` and `run_analysis()` in
  `cli/main.py` collects streamed state chunks in memory.
- Checkpoint status is logged by `TradingAgentsGraph.propagate()`.

There is no no-op/local/external tracer abstraction, JSONL trace schema,
redaction layer, node/tool duration and error records, configuration hash,
prompt hash, evidence/citation events, cost-estimation guard, or optional
observability package extra. The in-memory variable named `trace` is graph
state aggregation, not durable observability. LangSmith can potentially be
enabled through LangChain environment conventions, but the repository neither
configures nor documents it.

### 6. Configuration and CLI — **Partially implemented**

Configuration already has a single env-to-key registry, `_ENV_OVERRIDES`, and
typed coercion in `tradingagents/default_config.py`. CLI analyst selection uses
`AnalystType`, `ANALYST_ORDER`, and `select_analysts()` in `cli/models.py` and
`cli/utils.py`. The run path normalizes analyst order in `run_analysis()` in
`cli/main.py`, and `tests/test_env_overrides.py` plus
`tests/test_cli_config_precedence.py` verify precedence behavior.

No operational analyst option, citation-validation/temporal-grounding toggle,
local/external tracing toggle, trace path, or fixture/offline evaluation
command exists. New defaults must preserve the four-analyst default selection
and keep all extension features disabled unless explicitly selected.

### 7. Checkpointing and decision memory — **Already implemented**

- `get_checkpointer()`, `thread_id()`, `checkpoint_step()`, and
  `clear_checkpoint()` in `tradingagents/graph/checkpointer.py` implement
  per-ticker SQLite LangGraph checkpoints.
- `TradingAgentsGraph._run_signature()` prevents resumes across graph-shape,
  debate-depth, risk-depth, and asset-mode changes.
- `TradingMemoryLog` in `tradingagents/agents/utils/memory.py` implements the
  append-only decision log, outcome resolution, rotation, and prior-context
  injection.
- `tests/test_checkpoint_resume.py` and `tests/test_memory_log.py` provide
  broad deterministic coverage.

The extension should add evidence/tracing state without removing or bypassing
either persistence mechanism, and the operational analyst key must naturally
participate in the existing checkpoint signature.

### 8. Reporting — **Partially implemented**

`write_report_tree()` in `tradingagents/reporting.py` produces per-team Markdown
files and a consolidated report, and `TradingAgentsGraph.save_reports()` plus
the CLI share it. It does not render an operational report, structured
evidence, citation-validation findings, or a consolidated Sources section.
Legacy states without evidence metadata must continue to render unchanged.

### 9. Packaging and CI — **Already implemented, extension work required**

`pyproject.toml` uses setuptools, exposes the `tradingagents` console script,
defines `dev` and `bedrock` optional extras, and configures pytest and Ruff.
`.github/workflows/ci.yml` runs the full test matrix on Python 3.10–3.13, a
clean-install import smoke, and strict full-repository Ruff linting. The
observability integration should follow this optional-extra convention. An
offline evaluation smoke can join CI only if it stays deterministic, fast,
credential-free, and network-free.

## Gap-driven implementation plan

1. Add reusable typed evidence, claim, source consolidation, temporal
   validation, and report rendering utilities. Keep legacy prose-only reports
   valid.
2. Add an offline-capable operational evidence provider through the existing
   dataflow router, then build a structured Operational Signals Analyst using
   the existing analyst factory and state conventions.
3. Register the optional `operational` key across graph planning, ToolNodes,
   conditional routing, downstream debate context, CLI selection, reporting,
   and checkpoint signatures.
4. Add disabled, local JSONL, and external-environment observability modes with
   redaction and optional dependencies.
5. Build a committed synthetic-fixture evaluation package with deterministic
   metrics, optional model-assisted evaluation interfaces, baseline labels,
   and JSON/CSV/Markdown output.
6. Add focused tests and documentation that explicitly distinguishes upstream
   components from fork-specific additions.

No part of this plan evaluates or promises trading profitability. The extension
is for research-quality analysis, evidence traceability, and engineering
evaluation; it is not financial advice.
