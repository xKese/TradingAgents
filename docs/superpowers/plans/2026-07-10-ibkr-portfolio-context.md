# IBKR Portfolio Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `batch-analyze` use a single frozen snapshot from live read-only TWS and produce account-aware ticker decisions plus a coordinated portfolio review.

**Architecture:** A focused `tradingagents.ibkr` module loads and sanitizes one account snapshot through `ib_async`. The snapshot is passed through existing graph state only to decision-stage prompts, while a separate post-batch reviewer reconciles successful ticker decisions into Markdown and CSV artifacts.

**Tech Stack:** Python 3.10+, `ib_async`, Pydantic, LangGraph, Typer, pytest, existing TradingAgents LLM client abstractions.

## Global Constraints

- Connect to live TWS at `127.0.0.1:7496`; TWS must enforce API read-only mode.
- Support exactly one managed account and fail rather than guess when more are returned.
- Load the account snapshot exactly once before any batch LLM calls.
- Never place, modify, or cancel orders; do not expose execution methods.
- Do not save or prompt with account IDs, usernames, credentials, tokens, or connection secrets.
- Preserve existing `analyze` and `batch-analyze` behavior when `--ibkr-context` is absent.
- Keep research agents portfolio-blind; only Trader, risk analysts, Portfolio Manager, and post-batch reviewer receive portfolio context.
- Use the Miniconda `tradingagents` environment for verification.
- Preserve unrelated working-tree changes, especially current edits in `cli/main.py`, `tests/test_cli_batch_analyze.py`, and `tradingagents/graph/trading_graph.py`.

---

## File Structure

- Create `tradingagents/ibkr.py`: serializable snapshot types, TWS loader, validation, sanitization, ticker matching, and prompt rendering.
- Create `tests/test_ibkr.py`: deterministic fake-client tests for the loader and rendering.
- Modify `pyproject.toml`: add `ib_async` runtime dependency.
- Modify `tradingagents/agents/utils/agent_states.py`: add `portfolio_context` state field.
- Modify `tradingagents/graph/propagation.py`: initialize optional portfolio context.
- Modify `tradingagents/graph/trading_graph.py`: accept and forward optional portfolio context.
- Modify `tradingagents/agents/trader/trader.py`: include ticker-aware account context in the Trader prompt.
- Modify the three files in `tradingagents/agents/risk_mgmt/`: include portfolio context in risk debate prompts.
- Modify `tradingagents/agents/managers/portfolio_manager.py`: include portfolio context and account-aware decision rules.
- Create `tradingagents/portfolio_review.py`: post-batch LLM prompt, parsed advisory action model, Markdown/CSV writers.
- Create `tests/test_portfolio_review.py`: reviewer prompt and artifact tests.
- Modify `cli/main.py`: add options, preflight snapshot, shared propagation context, sanitized snapshot write, and reviewer invocation.
- Modify `tests/test_cli_batch_analyze.py`: CLI orchestration, fail-fast, and regression coverage.
- Modify `.env.example`: document optional IBKR connection settings.

---

### Task 1: Read-only IBKR snapshot loader

**Files:**
- Create: `tradingagents/ibkr.py`
- Create: `tests/test_ibkr.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `load_portfolio_snapshot(host: str, port: int, client_id: int, timeout: float = 10.0) -> dict`
- Produces: `validate_portfolio_snapshot(snapshot: dict) -> None`
- Produces: `sanitize_portfolio_snapshot(snapshot: dict) -> dict`
- Produces: `render_portfolio_context(snapshot: dict, ticker: str) -> str`
- Raises: `IBKRPortfolioError` for connection, account-count, or inconsistent-position failures.

- [ ] **Step 1: Add the dependency and write failing loader tests**

Add `"ib_async>=2.0.1"` to `[project].dependencies`. Create fake objects and tests that do not contact TWS:

```python
from types import SimpleNamespace

import pytest

from tradingagents.ibkr import IBKRPortfolioError, load_portfolio_snapshot


class FakeIB:
    def connect(self, host, port, clientId, timeout, readonly):
        assert (host, port, clientId, readonly) == ("127.0.0.1", 7496, 71, True)
        self._connected = True

    def managedAccounts(self):
        return ["SECRET_ACCOUNT"]

    def accountSummary(self, account):
        return [
            SimpleNamespace(tag="NetLiquidation", value="7436.90", currency="AUD"),
            SimpleNamespace(tag="TotalCashValue", value="2582.97", currency="AUD"),
            SimpleNamespace(tag="GrossPositionValue", value="4853.93", currency="AUD"),
        ]

    def portfolio(self, account):
        contract = SimpleNamespace(symbol="OUST", localSymbol="OUST", secType="STK")
        return [SimpleNamespace(contract=contract, position=10, marketPrice=47.82,
            marketValue=478.20, averageCost=51.653, unrealizedPNL=-38.33)]

    def disconnect(self):
        self._connected = False


def test_load_snapshot_is_read_only_and_sanitized():
    snapshot = load_portfolio_snapshot("127.0.0.1", 7496, 71, ib_factory=FakeIB)
    assert snapshot["base_currency"] == "AUD"
    assert snapshot["positions"][0]["symbol"] == "OUST"
    assert snapshot["positions"][0]["quantity"] == 10
    assert "SECRET_ACCOUNT" not in repr(snapshot)


def test_multiple_accounts_fail():
    class MultipleAccounts(FakeIB):
        def managedAccounts(self):
            return ["A", "B"]

    with pytest.raises(IBKRPortfolioError, match="exactly one"):
        load_portfolio_snapshot("127.0.0.1", 7496, 71, ib_factory=MultipleAccounts)
```

- [ ] **Step 2: Run the tests and confirm the intended failure**

Run:

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m pytest -q tests/test_ibkr.py
```

Expected: collection fails because `tradingagents.ibkr` does not exist.

- [ ] **Step 3: Implement the minimal snapshot loader**

Implement `IBKRPortfolioError`, numeric parsing, exactly-one-account validation, a `readonly=True` connection, `try/finally` disconnect, supported account tags, stock-position extraction, and account-ID omission. Accept `ib_factory` as a keyword-only test seam and lazily import `IB` from `ib_async` when it is absent:

```python
def load_portfolio_snapshot(host, port, client_id, timeout=10.0, *, ib_factory=None):
    if ib_factory is None:
        from ib_async import IB
        ib_factory = IB
    ib = ib_factory()
    try:
        ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
        accounts = list(ib.managedAccounts())
        if len(accounts) != 1:
            raise IBKRPortfolioError("TWS must expose exactly one managed account")
        account = accounts[0]
        summary = _parse_account_summary(ib.accountSummary(account))
        positions = [_position_to_dict(item) for item in ib.portfolio(account)
                     if item.contract.secType == "STK"]
        snapshot = _build_snapshot(summary, positions)
        validate_portfolio_snapshot(snapshot)
        return sanitize_portfolio_snapshot(snapshot)
    except IBKRPortfolioError:
        raise
    except Exception as exc:
        raise IBKRPortfolioError(f"Unable to load TWS portfolio: {exc}") from exc
    finally:
        ib.disconnect()
```

- [ ] **Step 4: Add validation and rendering edge-case tests**

Add tests proving:

```python
def test_nonzero_gross_value_with_no_positions_fails(): ...
def test_missing_market_price_keeps_quantity_and_cost(): ...
def test_render_owned_ticker_contains_weight_and_rank(): ...
def test_render_absent_ticker_says_owned_false_after_complete_fetch(): ...
def test_ambiguous_symbol_is_marked_uncertain(): ...
def test_snapshot_never_contains_account_identifier(): ...
```

Use explicit expected fragments such as `"Owned: yes"`, `"Quantity: 10"`, and `"Portfolio weight: unavailable"`.

- [ ] **Step 5: Run focused tests and commit**

Run the Conda pytest command from Step 2. Expected: all `tests/test_ibkr.py` tests pass.

Commit only Task 1 files:

```powershell
git add -- pyproject.toml tradingagents/ibkr.py tests/test_ibkr.py
git commit -m "feat: load read-only IBKR portfolio snapshots"
```

---

### Task 2: Carry portfolio context through graph state

**Files:**
- Modify: `tradingagents/agents/utils/agent_states.py`
- Modify: `tradingagents/graph/propagation.py`
- Modify: `tradingagents/graph/trading_graph.py`
- Create: `tests/test_portfolio_context_state.py`

**Interfaces:**
- Consumes: sanitized snapshot dictionary from Task 1.
- Produces: `TradingAgentsGraph.propagate(..., portfolio_context: dict | None = None)`.
- Produces: state key `portfolio_context`, always a dictionary and empty by default.

- [ ] **Step 1: Write failing state-propagation tests**

```python
from tradingagents.graph.propagation import Propagator


def test_initial_state_defaults_to_empty_portfolio_context():
    state = Propagator().create_initial_state("OUST", "2026-07-09")
    assert state["portfolio_context"] == {}


def test_initial_state_preserves_frozen_portfolio_context():
    snapshot = {"base_currency": "AUD", "positions": [{"symbol": "OUST"}]}
    state = Propagator().create_initial_state(
        "OUST", "2026-07-09", portfolio_context=snapshot
    )
    assert state["portfolio_context"] is snapshot
```

Add a graph test with a mocked compiled workflow verifying `propagate` passes the supplied snapshot into `create_initial_state`.

- [ ] **Step 2: Run the focused test and confirm failure**

Run `python -m pytest -q tests/test_portfolio_context_state.py` through Conda. Expected: `create_initial_state` rejects `portfolio_context`.

- [ ] **Step 3: Add the state field and optional parameters**

Add to `AgentState`:

```python
portfolio_context: Annotated[dict, "Frozen sanitized IBKR account snapshot"]
```

Add `portfolio_context: dict | None = None` to `Propagator.create_initial_state`, return `portfolio_context or {}`, then add the same optional argument to `TradingAgentsGraph.propagate` and forward it. Do not change `propagate_analysts`; research-only execution remains portfolio-blind.

- [ ] **Step 4: Run focused and existing graph tests**

Run:

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m pytest -q tests/test_portfolio_context_state.py tests/test_technical_only_graph.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 2**

Stage only the exact Task 2 hunks because two files already contain unrelated edits. Inspect `git diff --cached` before committing.

```powershell
git commit -m "feat: carry portfolio context through graph state"
```

---

### Task 3: Make decision-stage agents account-aware

**Files:**
- Modify: `tradingagents/agents/trader/trader.py`
- Modify: `tradingagents/agents/risk_mgmt/aggressive_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/conservative_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/neutral_debator.py`
- Modify: `tradingagents/agents/managers/portfolio_manager.py`
- Modify: `tradingagents/ibkr.py`
- Create: `tests/test_portfolio_context_prompts.py`

**Interfaces:**
- Consumes: `render_portfolio_context(snapshot: dict, ticker: str) -> str`.
- Produces: `get_portfolio_context_from_state(state: dict) -> str` helper returning an empty string when no context exists.

- [ ] **Step 1: Write prompt-capture tests**

Create a recording fake LLM and assert Trader, each risk analyst, and Portfolio Manager prompts contain `"LIVE PORTFOLIO CONTEXT — READ ONLY"`, `"Quantity: 10"`, and `"Current portfolio weight"` for OUST. Add negative tests that existing analyst nodes do not reference `portfolio_context` and that empty context preserves old prompt behavior.

- [ ] **Step 2: Run prompt tests and verify failure**

Run the focused file through Conda. Expected: the captured prompts do not contain portfolio context.

- [ ] **Step 3: Add one shared prompt helper**

In `tradingagents/ibkr.py`:

```python
def get_portfolio_context_from_state(state: dict) -> str:
    snapshot = state.get("portfolio_context") or {}
    if not snapshot:
        return ""
    return render_portfolio_context(snapshot, state["company_of_interest"])
```

Use the helper in exactly five decision-stage nodes. Append explicit instructions:

```text
Use the live portfolio context when translating analysis into action. Never say
"initiate" for an owned ticker. Distinguish Hold existing, Add, Trim, Exit, and
Avoid; reconcile sizing with current whole shares, portfolio weight, available
cash, and concentration. Do not let average cost override current evidence.
```

- [ ] **Step 4: Run prompt and structured-agent regressions**

Run:

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m pytest -q tests/test_portfolio_context_prompts.py tests/test_structured_agents.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 3 files**

```powershell
git add -- tradingagents/ibkr.py tradingagents/agents/trader/trader.py tradingagents/agents/risk_mgmt/aggressive_debator.py tradingagents/agents/risk_mgmt/conservative_debator.py tradingagents/agents/risk_mgmt/neutral_debator.py tradingagents/agents/managers/portfolio_manager.py tests/test_portfolio_context_prompts.py
git commit -m "feat: make decision agents portfolio aware"
```

---

### Task 4: Build the coordinated portfolio reviewer

**Files:**
- Create: `tradingagents/portfolio_review.py`
- Create: `tests/test_portfolio_review.py`

**Interfaces:**
- Produces: `build_portfolio_review(snapshot: dict, rows: list[dict], decisions: dict[str, str], llm) -> PortfolioReview`.
- Produces: `write_portfolio_review(review: PortfolioReview, batch_dir: Path) -> tuple[Path, Path]`.
- Produces: `portfolio_review.md` and `portfolio_actions.csv`.

- [ ] **Step 1: Write failing model, prompt, and artifact tests**

Define expected action records:

```python
def test_review_flags_buy_conflict_for_largest_holding():
    review = build_portfolio_review(snapshot, rows, {"OUST": "Rating: Buy"}, fake_llm)
    prompt = fake_llm.last_prompt
    assert "OUST" in prompt
    assert "largest" in prompt.lower()
    assert "soft concentration warning: 10%" in prompt


def test_writers_create_markdown_and_csv(tmp_path):
    md, csv = write_portfolio_review(review, tmp_path)
    assert md.name == "portfolio_review.md"
    assert csv.name == "portfolio_actions.csv"
    assert "current_shares" in csv.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run and confirm module-not-found failure**

Run `tests/test_portfolio_review.py` through Conda. Expected: collection failure.

- [ ] **Step 3: Implement typed advisory output and deterministic writers**

Create Pydantic models with these exact fields:

```python
class PortfolioAction(BaseModel):
    ticker: str
    action: Literal["Add", "Hold existing", "Trim", "Exit", "Avoid", "Review"]
    priority: Literal["High", "Medium", "Low"]
    current_shares: float | None = None
    proposed_shares: float | None = None
    share_change: float | None = None
    current_weight_pct: float | None = None
    proposed_weight_pct: float | None = None
    rationale: str


class PortfolioReview(BaseModel):
    executive_assessment: str
    conflicts_and_overrides: list[str]
    risk_triggers: list[str]
    data_quality_warnings: list[str]
    actions: list[PortfolioAction]
```

Use the existing structured-output helper with a free-text fallback. The prompt must state that outputs are advisory, use whole shares where practical, preserve currency labels, include failed tickers as coverage warnings, and never recommend execution through an API.

- [ ] **Step 4: Add failed-ticker and mixed-currency tests**

Assert failed rows appear in prompt coverage warnings and mixed currency values are not summed into an invented total.

- [ ] **Step 5: Run focused tests and commit**

Expected: all portfolio-review tests pass.

```powershell
git add -- tradingagents/portfolio_review.py tests/test_portfolio_review.py
git commit -m "feat: add coordinated portfolio review"
```

---

### Task 5: Integrate IBKR preflight and reviewer into `batch-analyze`

**Files:**
- Modify: `cli/main.py`
- Modify: `tests/test_cli_batch_analyze.py`
- Modify: `.env.example`

**Interfaces:**
- Consumes: Task 1 loader and Task 4 reviewer.
- Changes: `_run_batch_analysis(..., portfolio_context: dict | None = None, decision_sink: dict[str, str] | None = None)` forwards one shared snapshot to every full `propagate` call and records successful final decisions without changing summary row columns.
- Changes: `batch_analyze` options `ibkr_context`, `ibkr_host`, `ibkr_port`, and `ibkr_client_id`.

- [ ] **Step 1: Write fail-first CLI orchestration tests**

Add tests that:

```python
def test_ibkr_snapshot_loads_once_before_batch(monkeypatch, tmp_path): ...
def test_ibkr_failure_prevents_graph_construction(monkeypatch): ...
def test_shared_snapshot_is_passed_to_every_propagate(monkeypatch, tmp_path): ...
def test_sanitized_snapshot_is_written_to_batch_dir(monkeypatch, tmp_path): ...
def test_portfolio_reviewer_runs_after_successful_reports(monkeypatch, tmp_path): ...
def test_batch_without_ibkr_preserves_existing_call_shape(monkeypatch, tmp_path): ...
```

The fake graph's `propagate` should record `portfolio_context` and assert object identity across tickers.

- [ ] **Step 2: Run focused tests and verify expected signature failures**

Run `tests/test_cli_batch_analyze.py` through Conda. Expected: new option/signature assertions fail while existing tests remain green.

- [ ] **Step 3: Add CLI options and fail-fast preflight**

Add Typer options with environment-backed defaults:

```python
ibkr_context: bool = typer.Option(False, "--ibkr-context"),
ibkr_host: str = typer.Option(os.getenv("TRADINGAGENTS_IBKR_HOST", "127.0.0.1"), "--ibkr-host"),
ibkr_port: int = typer.Option(int(os.getenv("TRADINGAGENTS_IBKR_PORT", "7496")), "--ibkr-port"),
ibkr_client_id: int = typer.Option(int(os.getenv("TRADINGAGENTS_IBKR_CLIENT_ID", "71")), "--ibkr-client-id"),
```

When enabled, call `load_portfolio_snapshot` before constructing any graphs or invoking LLMs. Print a non-sensitive summary only. Create the batch directory, save `portfolio_snapshot.json`, forward the snapshot, pass a local `decisions: dict[str, str]` as `decision_sink`, populate it after each successful full-graph result from `final_state["final_trade_decision"]`, invoke the reviewer, and write both reviewer artifacts. Do not add the full decision text to CSV/Markdown summary rows.

- [ ] **Step 4: Preserve technical-only behavior explicitly**

Reject `--ibkr-context` when the selected analyst path is technical-only, with a clear message that portfolio review requires full decision-stage analysis. Add a test for this combination rather than silently discarding the context.

- [ ] **Step 5: Document TWS settings and environment variables**

Add to `.env.example`:

```dotenv
# Live Trader Workstation read-only portfolio context
TRADINGAGENTS_IBKR_HOST=127.0.0.1
TRADINGAGENTS_IBKR_PORT=7496
TRADINGAGENTS_IBKR_CLIENT_ID=71
```

Document that TWS must have socket clients enabled and API read-only checked.

- [ ] **Step 6: Run CLI regression tests and commit exact hunks**

Run:

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m pytest -q tests/test_cli_batch_analyze.py tests/test_ibkr.py tests/test_portfolio_review.py
```

Expected: all pass. Because `cli/main.py` and its test already have user changes, stage only reviewed IBKR-related hunks and inspect the cached diff.

Commit:

```powershell
git commit -m "feat: add IBKR-aware batch analysis"
```

---

### Task 6: Full verification and live read-only acceptance

**Files:**
- Modify only if verification exposes a scoped defect in Task 1-5 files.

**Interfaces:**
- Validates the complete feature; produces no new architecture.

- [ ] **Step 1: Run formatting and static checks on touched files**

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m ruff check tradingagents/ibkr.py tradingagents/portfolio_review.py tradingagents/agents/trader/trader.py tradingagents/agents/risk_mgmt tradingagents/agents/managers/portfolio_manager.py tradingagents/graph cli/main.py tests/test_ibkr.py tests/test_portfolio_review.py tests/test_portfolio_context_state.py tests/test_portfolio_context_prompts.py tests/test_cli_batch_analyze.py
```

Expected: no errors.

- [ ] **Step 2: Run focused and full test suites**

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m pytest -q tests/test_ibkr.py tests/test_portfolio_context_state.py tests/test_portfolio_context_prompts.py tests/test_portfolio_review.py tests/test_cli_batch_analyze.py tests/test_structured_agents.py
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m pytest -q
```

Expected: both commands pass; record exact counts.

- [ ] **Step 3: Perform a snapshot-only live smoke check**

With live TWS open, socket API enabled, API read-only checked, and port `7496`, run:

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -c "from tradingagents.ibkr import load_portfolio_snapshot; s=load_portfolio_snapshot('127.0.0.1',7496,71); print({'base_currency':s['base_currency'],'net_liquidation':s['net_liquidation'],'cash':s['cash'],'positions':[(p['symbol'],p['quantity']) for p in s['positions']]})"
```

Expected: exit code 0 and values matching TWS; no account identifier appears.

- [ ] **Step 4: Compare snapshot against TWS**

Manually verify NAV, cash, every portfolio ticker quantity, average cost, market value, and base currency. If any material mismatch exists, stop before the LLM run and fix the loader with a regression test.

- [ ] **Step 5: Run one real shallow portfolio batch**

```powershell
& "$env:USERPROFILE\miniconda3\condabin\conda.bat" run -n tradingagents python -m cli.main batch-analyze --tickers AAOI,CCXI,DRAM,ENHA,HIMS,IREN,NOK,NUAI,ONDS,OUST,PENG,RKLB --ibkr-context --analysts all --depth shallow --provider openrouter --model deepseek/deepseek-v4-flash --no-checkpoint
```

Expected: per-ticker reports, sanitized `portfolio_snapshot.json`, `portfolio_review.md`, and `portfolio_actions.csv` in one batch directory.

- [ ] **Step 6: Inspect acceptance criteria and repository state**

Verify CCXI is described as owned, OUST is not described as a new position, concentration conflicts are surfaced, no account ID appears in artifacts, and TWS shows no API-created/modified/cancelled orders. Run `git status --short` and confirm unrelated pre-existing changes remain intact.

- [ ] **Step 7: Commit only verification fixes, if any**

If no fixes were required, do not create an empty commit. If scoped fixes were required, commit them with tests:

```powershell
git commit -m "fix: harden IBKR portfolio context"
```
