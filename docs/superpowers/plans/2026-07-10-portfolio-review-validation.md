# Portfolio Review Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee one accurate portfolio action per successful ticker and remove reviewer prose that contradicts authoritative IBKR weight facts.

**Architecture:** The IBKR snapshot records whether weights were reconciled to base NAV. After the LLM returns a `PortfolioReview`, a deterministic normalization pass fills missing actions from Portfolio Manager decisions and filters only provably false threshold/currency statements.

**Tech Stack:** Python 3.10+, Pydantic, regex, pytest, existing TradingAgents structured-output helpers.

## Global Constraints

- Preserve LLM-generated narrative except for explicit factual contradictions.
- Emit exactly one `PortfolioAction` for every ticker in `decisions`.
- Preserve existing valid model actions.
- Never infer an unknown share count; use `None` when parsing is inconclusive.
- Treat 10% as the existing soft concentration threshold.
- Preserve general AUD/USD economic-risk commentary.
- Do not change per-ticker agents, CLI behavior, or order safety.

---

### Task 1: Mark authoritative reconciled weights

**Files:**
- Modify: `tradingagents/ibkr.py`
- Modify: `tests/test_ibkr.py`

**Interfaces:**
- Produces: snapshot key `weights_reconciled_to_base_nav: bool`.

- [ ] **Step 1: Write the failing metadata test**

```python
def test_same_currency_weights_are_marked_reconciled_to_base_nav():
    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=CurrencyScaledIB
    )
    assert snapshot["weights_reconciled_to_base_nav"] is True
```

Add a mixed-currency fixture and assert the flag is `False`.

- [ ] **Step 2: Verify RED**

Run:

```powershell
& "$env:USERPROFILE\miniconda3\envs\tradingagents\python.exe" -m pytest -q tests/test_ibkr.py
```

Expected: failures because the metadata key is absent.

- [ ] **Step 3: Return reconciliation status from the weight helper**

Change `_reconcile_position_weights` to return `True` only for the same-currency gross-exposure reconciliation path and `False` otherwise. Store the result in the snapshot:

```python
weights_reconciled = _reconcile_position_weights(positions, summary)
snapshot = {
    **summary,
    "weights_reconciled_to_base_nav": weights_reconciled,
    "positions": positions,
}
```

- [ ] **Step 4: Verify GREEN and commit**

Run the Task 1 test command. Expected: all tests pass.

```powershell
git add -- tradingagents/ibkr.py tests/test_ibkr.py
git commit -m "fix: mark reconciled IBKR portfolio weights"
```

---

### Task 2: Normalize actions and factual claims

**Files:**
- Modify: `tradingagents/portfolio_review.py`
- Modify: `tests/test_portfolio_review.py`

**Interfaces:**
- Produces: `normalize_portfolio_review(review: PortfolioReview, snapshot: dict, decisions: dict[str, str]) -> PortfolioReview`.
- Produces: `_derive_action(ticker: str, decision: str, position: dict | None) -> PortfolioAction`.

- [ ] **Step 1: Write failing OUST and RKLB action tests**

```python
def test_missing_oust_action_is_derived_from_underweight_decision():
    review = PortfolioReview(
        executive_assessment="review",
        conflicts_and_overrides=[], risk_triggers=[],
        data_quality_warnings=[], actions=[],
    )
    decision = """**Rating**: Underweight
**Executive Summary**: Sell 2 of 10 OUST shares, reducing weight from 9.26% to 7.4%."""
    normalized = normalize_portfolio_review(
        review, reconciled_snapshot, {"OUST": decision}
    )
    action = normalized.actions[0]
    assert (action.action, action.current_shares, action.proposed_shares) == (
        "Trim", 10, 8
    )
    assert action.share_change == -2
    assert action.proposed_weight_pct == pytest.approx(7.408)


def test_missing_rklb_hold_action_keeps_two_shares():
    decision = """**Rating**: Hold
**Executive Summary**: Maintain the current 2-share RKLB position."""
    normalized = normalize_portfolio_review(
        empty_review, reconciled_snapshot, {"RKLB": decision}
    )
    assert normalized.actions[0].action == "Hold existing"
    assert normalized.actions[0].share_change == 0
```

- [ ] **Step 2: Write failing contradiction-filter tests**

```python
def test_below_threshold_exceeds_claim_is_removed():
    review.conflicts_and_overrides = ["OUST at 9.26% exceeds the 10% threshold."]
    normalized = normalize_portfolio_review(review, reconciled_snapshot, {})
    assert normalized.conflicts_and_overrides == []


def test_false_fx_warning_removed_but_fx_risk_preserved():
    review.data_quality_warnings = [
        "Weights cannot be reconciled without an FX conversion.",
        "AUD/USD currency exposure remains unhedged.",
    ]
    normalized = normalize_portfolio_review(review, reconciled_snapshot, {})
    assert normalized.data_quality_warnings == [
        "AUD/USD currency exposure remains unhedged."
    ]
```

Add a test proving an existing model action for a ticker is not replaced.

- [ ] **Step 3: Verify RED**

Run:

```powershell
& "$env:USERPROFILE\miniconda3\envs\tradingagents\python.exe" -m pytest -q tests/test_portfolio_review.py
```

Expected: import failure because `normalize_portfolio_review` is absent.

- [ ] **Step 4: Implement deterministic derivation**

Use case-insensitive regexes for rating, executive summary, `sell|trim N`, `N-share`, and `sell N of M`. Map ratings to actions, populate the current position from the snapshot, derive proposed shares only when explicit, and compute proposed weight as:

```python
proposed_weight = current_weight * proposed_shares / current_shares
```

Append derived actions after preserved model actions in decision order.

- [ ] **Step 5: Implement conservative contradiction filters**

When authoritative weights are present, remove only strings that simultaneously contain a known ticker, an `exceed` term, `10%`, and a current weight below 10. When `weights_reconciled_to_base_nav` is true, remove warnings containing a reconciliation-negation phrase such as `cannot reconcile`, `without conversion`, `no conversion`, or `USD values ... AUD NAV`. Do not remove strings containing only `currency risk`, `FX risk`, or `AUD/USD exposure`.

Update `_review_prompt` with:

```text
Portfolio weights are authoritative base-NAV percentages when
weights_reconciled_to_base_nav is true. Do not claim they lack FX conversion.
Return exactly one action for every successful ticker decision.
```

Call `normalize_portfolio_review` for structured and fallback results before returning.

- [ ] **Step 6: Verify focused regressions**

```powershell
& "$env:USERPROFILE\miniconda3\envs\tradingagents\python.exe" -m pytest -q tests/test_portfolio_review.py tests/test_ibkr.py tests/test_cli_ibkr_context.py
```

Expected: all pass and CSV writer tests include derived OUST action data.

- [ ] **Step 7: Run complete verification and commit**

```powershell
& "$env:USERPROFILE\miniconda3\envs\tradingagents\python.exe" -m ruff check tradingagents/ibkr.py tradingagents/portfolio_review.py tests/test_ibkr.py tests/test_portfolio_review.py
$env:DEEPSEEK_API_KEY='placeholder'
& "$env:USERPROFILE\miniconda3\envs\tradingagents\python.exe" -m pytest -q
```

Expected: lint passes; full suite passes with only documented optional skips.

```powershell
git add -- tradingagents/portfolio_review.py tests/test_portfolio_review.py
git commit -m "fix: validate portfolio review actions and facts"
git push fork codex/ibkr-portfolio-context
```

- [ ] **Step 8: Live OUST acceptance check**

Run one market-only OUST analysis with `--ibkr-context`. Verify `portfolio_actions.csv` contains a row and `portfolio_review.md` contains neither the false threshold claim nor false reconciliation warning. Do not place or modify orders.
