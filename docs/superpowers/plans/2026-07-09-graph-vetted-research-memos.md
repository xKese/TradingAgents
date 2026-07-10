# Graph-Vetted Research Memos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the research sleeve into a two-stage funnel: the brain researches every screen passer into a first-cut memo; the multi-agent TradingAgents graph vets only the brain's "buy" memos and becomes a REQUIRED gate — only graph-confirmed memos trade.

**Architecture:** The brain's buy memos are written `pending_vetting` instead of `open` (one-line change). A new `ops/research/vetting.py` distills each pending memo into a deterministic brief, injects it into the graph via a new `research_memo_context` state field (fundamentals analyst + bull/bear prompts only), maps the graph's native 5-tier rating to verdict+conviction in our own code (Buy→confirm/high, Overweight→confirm/medium, else reject), runs one bounded gate-validated risk-falsifier extraction pass, and promotes/rejects the memo. Vetting runs as a second overnight stage inside the existing 00:00–08:00 deadline-boxed window.

**Tech Stack:** Python 3.12, Pydantic v2, stdlib sqlite3, LangChain/LangGraph, pytest.

**Spec (source of truth):** `docs/superpowers/specs/2026-07-10-graph-vetted-research-memos-design.md`

## Global Constraints

- **One branch:** all tasks land on `feat/graph-vetted-research-memos`. Work in `/Users/frednick/Code/TradingAgents` ONLY (never `/Users/frednick/Code/TradingAgents-live`).
- **Momentum path byte-identical:** with `research_memo_context=""`, the graph's initial state (existing keys) and every rendered analyst/debater prompt must be byte-identical to today. Pinned by golden-string tests in Task 5. Do NOT "fix" pre-existing prompt quirks (e.g. the fundamentals analyst's `system_message` is a **tuple** today via a trailing comma — its `str()` repr is what renders; leave it).
- **Do NOT touch `past_context`** — it's the memory system's channel and reaches only the portfolio manager. Injection uses the new `research_memo_context` field only.
- New journal event kinds MUST be added to BOTH `events.BUILDERS` and `events.AUDIT_ONLY` or `tests/ops/notify/test_policy.py` fails.
- **Tests:** run targeted test files per task, plus `python -m pytest tests/ops` (NOT the full `tests/` sweep — it times out on slow/opt-in live suites). Known pre-existing flake to ignore: `test_daily_overview_tick_writes_file_and_records_gate_event` (local-timezone issue, unrelated).
- **Never run the live pipeline, `ops research kick`, or start ds4.** Build + test only.
- **Git hygiene:** `git add` specific paths only — NEVER `git add -A`/`git add .`. The working tree has stray unrelated edits to `main.py` (repo root) and `tradingagents/dataflows/reddit.py` that must NOT be committed.
- Commit style (from repo history): `feat(research): ...`, `fix(research): ...`, `test(...): ...`. End commit messages with the Co-Authored-By/Claude-Session trailer per harness instructions.
- Python: use `python -m pytest ...` from the repo root.

---

### Task 1: Memo schema — new statuses + `VettingResult` provenance block

**Files:**
- Modify: `tradingagents/memos/schema.py`
- Test: `tests/test_memo_store.py` (extend)

**Interfaces:**
- Produces: `MemoStatus = Literal["pending_vetting", "open", "rejected", "passed", "resolved"]`; `VettingVerdict = Literal["confirm", "reject"]`; `class VettingResult(BaseModel)` with fields `verdict: VettingVerdict`, `rating: str`, `conviction_before: ConvictionTier`, `conviction_after: ConvictionTier | None = None`, `added_falsifier_indices: list[int] = []`, `rationale: str = ""`, `vetted_by_model: str = ""`, `vetted_at: datetime` (default now, UTC); `Memo.vetting: VettingResult | None = None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_memo_store.py`):

```python
def test_memo_status_accepts_vetting_lifecycle_values():
    """pending_vetting and rejected are valid memo statuses (graph-vetting funnel)."""
    memo = make_memo(status="pending_vetting")
    assert memo.status == "pending_vetting"
    memo2 = make_memo(status="rejected")
    assert memo2.status == "rejected"


def test_vetting_result_round_trips_on_memo():
    from tradingagents.memos.schema import VettingResult

    vetting = VettingResult(
        verdict="confirm", rating="Buy", conviction_before="starter",
        conviction_after="high", added_falsifier_indices=[2, 3],
        rationale="judge liked it", vetted_by_model="openai_compatible:ds4",
    )
    memo = make_memo(vetting=vetting)
    restored = Memo.model_validate_json(memo.model_dump_json())
    assert restored.vetting is not None
    assert restored.vetting.verdict == "confirm"
    assert restored.vetting.rating == "Buy"
    assert restored.vetting.conviction_before == "starter"
    assert restored.vetting.conviction_after == "high"
    assert restored.vetting.added_falsifier_indices == [2, 3]


def test_vetting_result_reject_needs_no_conviction_after():
    from tradingagents.memos.schema import VettingResult

    vetting = VettingResult(
        verdict="reject", rating="Hold", conviction_before="medium",
        rationale="debate found the thesis weak",
    )
    assert vetting.conviction_after is None


def test_memo_vetting_defaults_none():
    assert make_memo().vetting is None
```

Note: `tests/test_memo_store.py` already has a `make_memo(**overrides)` helper (or an equivalent memo-construction helper) — read the file first and reuse its existing helper; if its name differs, adapt these tests to it rather than adding a duplicate.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_memo_store.py -v -k "vetting or lifecycle_values"`
Expected: FAIL (ValidationError on `pending_vetting` status; ImportError on `VettingResult`).

- [ ] **Step 3: Implement.** In `tradingagents/memos/schema.py`:

Replace:
```python
MemoStatus = Literal["open", "passed", "resolved"]
```
with:
```python
# Lifecycle: the brain writes a buy thesis as ``pending_vetting``; the graph
# vetting stage adjudicates it to ``open`` (tradeable) or ``rejected`` (never
# traded, kept as corpus). ``passed`` = the brain itself declined to buy
# (shadow-tracked). Only ``open`` memos trade — graph confirmation is a
# required gate purely by construction.
MemoStatus = Literal["pending_vetting", "open", "rejected", "passed", "resolved"]

VettingVerdict = Literal["confirm", "reject"]
```

Add after the `Resolution` class:
```python
class VettingResult(BaseModel):
    """Provenance of the graph-vetting adjudication (funnel stage 2).

    Report-time provenance only — never a sizing input (mirrors
    ``authored_by_model``). ``rating`` is the graph's native 5-tier word
    (Buy/Overweight/Hold/Underweight/Sell); the verdict/conviction mapping
    from that rating lives in ``ops/research/vetting.py``, not here.
    """

    verdict: VettingVerdict
    rating: str = Field(
        description="The graph's native 5-tier rating word that drove the verdict."
    )
    conviction_before: ConvictionTier = Field(
        description="The brain's conviction tier at vetting time."
    )
    conviction_after: ConvictionTier | None = Field(
        default=None,
        description="Graph-mapped conviction applied on confirm; None on reject.",
    )
    added_falsifier_indices: list[int] = Field(
        default_factory=list,
        description="Indices into Memo.falsifiers appended by the risk-debate extraction.",
    )
    rationale: str = Field(
        default="", description="Short judge-decision summary explaining the verdict."
    )
    vetted_by_model: str = Field(
        default="", description="Model spec of the graph that vetted this memo."
    )
    vetted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

Add to `Memo` (right after the `authored_by_model` field):
```python
    vetting: VettingResult | None = Field(
        default=None,
        description=(
            "Graph-vetting adjudication provenance; None for memos that have "
            "not been vetted (including pre-funnel memos). Never a sizing input."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_memo_store.py -v`
Expected: PASS (all — including pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/memos/schema.py tests/test_memo_store.py
git commit -m "feat(memos): pending_vetting/rejected statuses + VettingResult provenance"
```

---

### Task 2: Memo store — vetting queue + adjudication persistence + trade-gate pin

**Files:**
- Modify: `tradingagents/memos/store.py`
- Test: `tests/test_memo_store.py` (extend)

**Interfaces:**
- Consumes: Task 1's `MemoStatus`, `VettingResult`.
- Produces: `MemoStore.pending_vetting_memos() -> list[Memo]` (oldest-first); `MemoStore.apply_vetting(memo: Memo) -> None` — persists a vetted memo whose in-memory `status` is `"open"` or `"rejected"` and whose `vetting` is set; refuses to touch a row whose stored status is not `pending_vetting` (raises `ValueError`), raises `KeyError` if the memo doesn't exist.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_memo_store.py`; reuse the existing memo factory + tmp-path store fixtures the file already uses):

```python
def test_pending_vetting_memos_returns_queue_oldest_first(store):
    older = make_memo(status="pending_vetting",
                      created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    newer = make_memo(status="pending_vetting",
                      created_at=datetime(2026, 7, 5, tzinfo=timezone.utc))
    open_memo = make_memo(status="open")
    passed = make_memo(status="passed")
    for m in (newer, open_memo, older, passed):
        store.save(m)
    queue = store.pending_vetting_memos()
    assert [m.memo_id for m in queue] == [older.memo_id, newer.memo_id]


def test_pending_vetting_memo_is_not_open_and_never_trades(store):
    """THE gate: a pending_vetting memo must not appear in open_memos()."""
    store.save(make_memo(status="pending_vetting"))
    assert store.open_memos() == []
    assert len(store.pending_vetting_memos()) == 1


def test_apply_vetting_confirm_promotes_to_open(store):
    from tradingagents.memos.schema import VettingResult

    memo = make_memo(status="pending_vetting", conviction_tier="starter")
    store.save(memo)
    memo.status = "open"
    memo.conviction_tier = "high"
    memo.vetting = VettingResult(
        verdict="confirm", rating="Buy", conviction_before="starter",
        conviction_after="high",
    )
    store.apply_vetting(memo)
    got = store.get(memo.memo_id)
    assert got.status == "open"
    assert got.conviction_tier == "high"
    assert got.vetting.verdict == "confirm"
    assert store.pending_vetting_memos() == []
    assert [m.memo_id for m in store.open_memos()] == [memo.memo_id]


def test_apply_vetting_reject_marks_rejected(store):
    from tradingagents.memos.schema import VettingResult

    memo = make_memo(status="pending_vetting")
    store.save(memo)
    memo.status = "rejected"
    memo.vetting = VettingResult(
        verdict="reject", rating="Hold", conviction_before=memo.conviction_tier,
    )
    store.apply_vetting(memo)
    got = store.get(memo.memo_id)
    assert got.status == "rejected"
    assert store.open_memos() == []
    assert store.pending_vetting_memos() == []


def test_apply_vetting_refuses_non_pending_row(store):
    from tradingagents.memos.schema import VettingResult

    memo = make_memo(status="open")
    store.save(memo)
    memo.status = "open"
    memo.vetting = VettingResult(
        verdict="confirm", rating="Buy", conviction_before=memo.conviction_tier,
        conviction_after="high",
    )
    with pytest.raises(ValueError, match="pending_vetting"):
        store.apply_vetting(memo)


def test_apply_vetting_requires_vetting_block_and_final_status(store):
    memo = make_memo(status="pending_vetting")
    store.save(memo)
    memo.status = "open"          # vetting block missing
    with pytest.raises(ValueError, match="vetting"):
        store.apply_vetting(memo)


def test_apply_vetting_unknown_memo_raises_keyerror(store):
    from tradingagents.memos.schema import VettingResult

    memo = make_memo(status="pending_vetting")  # never saved
    memo.status = "rejected"
    memo.vetting = VettingResult(
        verdict="reject", rating="Sell", conviction_before=memo.conviction_tier,
    )
    with pytest.raises(KeyError):
        store.apply_vetting(memo)
```

(If the file has no `store` fixture, construct `MemoStore(tmp_path / "memos.sqlite")` inline the way its existing tests do.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_memo_store.py -v -k "pending_vetting or apply_vetting"`
Expected: FAIL with `AttributeError: 'MemoStore' object has no attribute 'pending_vetting_memos'`.

- [ ] **Step 3: Implement.** In `tradingagents/memos/store.py`:

Add to the write path (after `mark_passed`):
```python
    def apply_vetting(self, memo: Memo) -> None:
        """Persist a graph-vetting adjudication: pending_vetting -> open|rejected.

        The caller (ops/research/vetting.py) mutates the memo in memory
        (status, conviction_tier, appended falsifiers, vetting provenance);
        this method persists it, refusing anything that is not a
        pending_vetting row transitioning to a final vetted status — the
        stored-status check makes a double-vet or a race with resolution a
        loud error instead of a silent overwrite.
        """
        if memo.vetting is None:
            raise ValueError(f"memo {memo.memo_id}: apply_vetting requires a vetting block")
        if memo.status not in ("open", "rejected"):
            raise ValueError(
                f"memo {memo.memo_id}: apply_vetting expects status open/rejected, "
                f"got {memo.status!r}"
            )
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM memos WHERE memo_id = ?", (memo.memo_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"no memo with id {memo.memo_id!r}")
            if row["status"] != "pending_vetting":
                raise ValueError(
                    f"memo {memo.memo_id!r} is {row['status']!r}, not pending_vetting"
                )
            conn.execute(
                "UPDATE memos SET status = ?, conviction_tier = ?, payload = ? "
                "WHERE memo_id = ?",
                (memo.status, memo.conviction_tier, memo.model_dump_json(), memo.memo_id),
            )
```

Add to the read path (after `open_memos`):
```python
    def pending_vetting_memos(self) -> list[Memo]:
        """The graph-vetting queue: brain-buys awaiting adjudication, oldest-first.

        Every pending_vetting memo is a brain-buy by construction (the pass
        path goes straight to ``passed``), so no recommendation filter exists.
        """
        memos = self.list(status="pending_vetting")
        return sorted(memos, key=lambda m: m.created_at)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_memo_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tradingagents/memos/store.py tests/test_memo_store.py
git commit -m "feat(memos): vetting queue + apply_vetting adjudication persistence"
```

---

### Task 3: Brain — buy memos enter the vetting queue

**Files:**
- Modify: `ops/research/brain.py` (one line: `status="open"` → `status="pending_vetting"` in the `Memo(...)` construction inside `research_hit`, currently line ~384)
- Test: `tests/ops/research/test_brain.py` (update existing buy-path assertions + add one explicit test)

**Interfaces:**
- Consumes: Task 1 statuses.
- Produces: brain buy memos stored with `status == "pending_vetting"`; pass path (`mark_passed` → `passed`) byte-for-byte unchanged.

- [ ] **Step 1: Read `tests/ops/research/test_brain.py`** and find tests asserting the stored memo's status after a "buy" recommendation (and any asserting `open`). Add the new test:

```python
def test_buy_memo_is_stored_pending_vetting(...existing fixtures...):
    """A brain buy is NOT tradeable until the graph confirms it: it enters
    the vetting queue, never open_memos()."""
    # drive research_hit with a draft whose recommendation == "buy",
    # using the file's existing fake-LLM/fixture pattern
    outcome = research_hit(...)
    assert outcome.status == "researched"
    memo = memo_store.get(outcome.memo_id)
    assert memo.status == "pending_vetting"
    assert memo_store.open_memos() == []
    assert [m.memo_id for m in memo_store.pending_vetting_memos()] == [memo.memo_id]
```

Adapt to the file's existing fixture style — do not invent new plumbing; the file already fakes `evidence_llm`/`thesis_llm`/EDGAR. Also update any existing test that asserts a buy memo is `open` to expect `pending_vetting`. Do NOT touch pass-path tests (still `passed`).

- [ ] **Step 2: Run to verify the new test fails**

Run: `python -m pytest tests/ops/research/test_brain.py -v`
Expected: new test FAILS (memo stored `open`).

- [ ] **Step 3: Implement.** In `ops/research/brain.py`, in `research_hit`, change the `Memo(...)` construction:

```python
        memo = Memo(
            ticker=symbol, as_of_date=today, entry_price_ref=float(price),
            evidence=kept, status="pending_vetting",
            authored_by_model=thesis_model_spec or "",
            **draft.model_dump(exclude={"recommendation"}),
        )
```

(The pass branch is unchanged: `mark_passed` flips `pending_vetting` → `passed`, which it already supports since it only rejects `resolved`.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ops/research/test_brain.py tests/ops/research/test_drain.py -v`
Expected: PASS (fix any drain-test assertions that assumed `open`).

- [ ] **Step 5: Commit**

```bash
git add ops/research/brain.py tests/ops/research/test_brain.py
git commit -m "feat(research): brain buys enter the graph-vetting queue (pending_vetting)"
```
(Include `tests/ops/research/test_drain.py` in the add if it needed updates.)

---

### Task 4: Deterministic memo → research brief

**Files:**
- Create: `ops/research/memo_brief.py`
- Test: `tests/ops/research/test_memo_brief.py`

**Interfaces:**
- Consumes: `tradingagents.memos.schema.Memo`.
- Produces: `build_research_brief(memo: Memo) -> str` — pure, deterministic, bounded (`MAX_BRIEF_CHARS = 6000`, `MAX_EVIDENCE_ITEMS = 8`, `MAX_QUOTE_CHARS = 240`). Task 8 injects this string as `research_context`.

- [ ] **Step 1: Write the failing tests** (`tests/ops/research/test_memo_brief.py`). Build memos with the same factory style as `tests/ops/research/test_memo_validation.py` (read it first; construct value and event memos inline if there is no shared factory):

```python
"""build_research_brief: deterministic memo distillation for graph injection."""
from datetime import date

from ops.research.memo_brief import (
    MAX_BRIEF_CHARS, MAX_EVIDENCE_ITEMS, build_research_brief,
)
from tradingagents.memos.schema import (
    Catalyst, EventThesis, EvidenceItem, Falsifier, Memo, ValueThesis,
)


def _value_memo(**overrides):
    base = dict(
        ticker="ACME", as_of_date=date(2026, 7, 1), thesis_type="value",
        thesis="Mispriced earning power after a guidance-cut selloff.",
        evidence=[
            EvidenceItem(claim=f"claim {i}", source_type="filing",
                         source_ref=f"0001:mdna:{i}", quote=f"quote {i}")
            for i in range(12)
        ],
        value_block=ValueThesis(
            why_cheap="Segment X is in decline and the market extrapolates it.",
            change_trigger="New CEO with cost-cut mandate.",
            normalized_earnings_view="Through-cycle EPS ~2x screen optics.",
            quality_assessment="Net cash, 20% ROIC ex the declining segment.",
        ),
        conviction_tier="medium", entry_price_ref=10.0,
        price_target_low=15.0, price_target_high=20.0,
        expected_holding_months=12,
        must_be_true=["Segment Y keeps growing", "No covenant breach"],
        falsifiers=[
            Falsifier(description="Gross margin collapses",
                      check_type="fundamental", metric="gross_margin_pct",
                      operator="<", threshold=30.0),
            Falsifier(description="Story breaks down", check_type="event"),
        ],
    )
    base.update(overrides)
    return Memo(**base)


def test_brief_contains_the_load_bearing_fields():
    brief = build_research_brief(_value_memo())
    assert "ACME" in brief
    assert "Mispriced earning power" in brief
    assert "Segment X is in decline" in brief          # why_cheap
    assert "Segment Y keeps growing" in brief          # must_be_true
    assert "claim 0" in brief and "[0001:mdna:0]" in brief  # cited evidence
    assert "gross_margin_pct < 30.0" in brief          # machine falsifier
    assert "Story breaks down" in brief                # prose falsifier
    assert "15.0" in brief and "20.0" in brief         # targets


def test_brief_caps_evidence_at_top_n():
    brief = build_research_brief(_value_memo())
    assert f"claim {MAX_EVIDENCE_ITEMS - 1}" in brief
    assert f"claim {MAX_EVIDENCE_ITEMS}" not in brief


def test_brief_is_deterministic():
    memo = _value_memo()
    assert build_research_brief(memo) == build_research_brief(memo)


def test_brief_is_bounded_on_a_monster_memo():
    memo = _value_memo(
        thesis="T" * 20000,
        must_be_true=["M" * 2000] * 10,
    )
    assert len(build_research_brief(memo)) <= MAX_BRIEF_CHARS


def test_brief_truncates_long_quotes():
    memo = _value_memo(evidence=[
        EvidenceItem(claim="c", source_type="filing", source_ref="0001:mdna",
                     quote="q" * 5000),
    ])
    brief = build_research_brief(memo)
    assert "q" * 241 not in brief


def test_event_memo_renders_event_block():
    memo = _value_memo(
        thesis_type="event", value_block=None,
        event_block=EventThesis(
            event_type="spinoff", seller_identity="index funds",
            why_non_economic="Forced deletion selling at any price.",
            pressure_end_estimate=date(2026, 9, 30),
            key_dates=[Catalyst(description="distribution",
                                expected_date=date(2026, 8, 1), hard_date=True)],
        ),
    )
    brief = build_research_brief(memo)
    assert "spinoff" in brief
    assert "index funds" in brief
    assert "Forced deletion selling" in brief


def test_evidence_without_quote_is_fine():
    memo = _value_memo(evidence=[
        EvidenceItem(claim="bare claim", source_type="filing", source_ref="0001:rf"),
    ])
    assert "bare claim" in build_research_brief(memo)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ops/research/test_memo_brief.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'ops.research.memo_brief'`.

- [ ] **Step 3: Implement** `ops/research/memo_brief.py`:

```python
"""Deterministic memo -> research-brief distillation for graph injection.

The graph vets a brain memo by receiving a compact, labelled summary of it
as ``research_memo_context``. This is a pure function — no LLM — so the
brief is reproducible and its length is bounded (ds4's context is finite;
the brief must never crowd out the analysts' own tool output).
"""

from __future__ import annotations

from tradingagents.memos.schema import Memo

MAX_EVIDENCE_ITEMS = 8
MAX_QUOTE_CHARS = 240
MAX_BRIEF_CHARS = 6000


def build_research_brief(memo: Memo) -> str:
    """Distill a memo into a labelled plain-text brief, bounded in length."""
    lines: list[str] = [
        f"RESEARCH MEMO BRIEF — {memo.ticker} "
        f"(as of {memo.as_of_date.isoformat()}, {memo.thesis_type} thesis, "
        f"researcher conviction: {memo.conviction_tier})",
        f"THESIS: {memo.thesis}",
    ]
    if memo.value_block is not None:
        vb = memo.value_block
        lines += [
            f"WHY CHEAP (the bear's answer): {vb.why_cheap}",
            f"CHANGE TRIGGER: {vb.change_trigger}",
            f"NORMALIZED EARNINGS VIEW: {vb.normalized_earnings_view}",
            f"QUALITY: {vb.quality_assessment}",
        ]
    if memo.event_block is not None:
        eb = memo.event_block
        lines += [
            f"EVENT: {eb.event_type} — seller: {eb.seller_identity}",
            f"WHY NON-ECONOMIC: {eb.why_non_economic}",
        ]
        if eb.pressure_end_estimate is not None:
            lines.append(
                f"PRESSURE END (est.): {eb.pressure_end_estimate.isoformat()}"
            )
        for kd in eb.key_dates:
            when = kd.expected_date.isoformat() if kd.expected_date else "date TBD"
            hard = " [hard date]" if kd.hard_date else ""
            lines.append(f"KEY DATE: {when}{hard} — {kd.description}")
    lines.append(
        f"PRICE: ref {memo.entry_price_ref}, "
        f"target {memo.price_target_low}-{memo.price_target_high}, "
        f"horizon {memo.expected_holding_months}mo"
    )
    lines.append("MUST BE TRUE:")
    lines += [f"- {m}" for m in memo.must_be_true]
    shown = min(len(memo.evidence), MAX_EVIDENCE_ITEMS)
    lines.append(f"EVIDENCE (cited; top {shown} of {len(memo.evidence)}):")
    for item in memo.evidence[:MAX_EVIDENCE_ITEMS]:
        quote = f' "{item.quote[:MAX_QUOTE_CHARS]}"' if item.quote else ""
        lines.append(f"- {item.claim} [{item.source_ref}]{quote}")
    lines.append("FALSIFIERS (pre-committed exit conditions):")
    for f in memo.falsifiers:
        mech = (
            f" [{f.metric} {f.operator} {f.threshold}]"
            if f.metric and f.operator is not None and f.threshold is not None
            else ""
        )
        lines.append(f"- {f.description}{mech}")
    return "\n".join(lines)[:MAX_BRIEF_CHARS]
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ops/research/test_memo_brief.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/research/memo_brief.py tests/ops/research/test_memo_brief.py
git commit -m "feat(research): deterministic memo->brief distillation for graph injection"
```

---

### Task 5: Graph injection channel — `research_memo_context` (byte-identical when empty)

**Files:**
- Modify: `tradingagents/agents/utils/agent_states.py`
- Modify: `tradingagents/graph/propagation.py`
- Modify: `tradingagents/graph/trading_graph.py`
- Modify: `tradingagents/agents/analysts/fundamentals_analyst.py`
- Modify: `tradingagents/agents/researchers/bull_researcher.py`
- Modify: `tradingagents/agents/researchers/bear_researcher.py`
- Test: `tests/test_research_memo_injection.py` (new)

**Interfaces:**
- Produces: `AgentState.research_memo_context: str`; `Propagator.create_initial_state(..., research_memo_context: str = "")`; `TradingAgentsGraph.propagate(company_name, trade_date, asset_type="stock", research_memo_context="")` threading into `_run_graph` → `create_initial_state`. Task 6 calls the new `propagate` kwarg.
- **#1 correctness guarantee:** empty context ⇒ initial state (existing keys) and rendered fundamentals/bull/bear prompts are byte-identical to today. The tests below pin this with golden strings copied verbatim from the CURRENT source. **Copy the golden templates from the current file contents, not from this plan, if they ever disagree.**

- [ ] **Step 1: Write the failing tests** — `tests/test_research_memo_injection.py`:

```python
"""Backward-compat pin for the research_memo_context injection channel.

The graph is shared with the live momentum sleeve. With an empty context,
initial state (existing keys) and the rendered fundamentals/bull/bear
prompts must be BYTE-IDENTICAL to the pre-injection code. The golden
templates below are copied verbatim from the pre-change source; if these
tests fail, the momentum path changed — that is a bug in the change, not
in the tests.
"""
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
)
from tradingagents.graph.propagation import Propagator


class RecorderLLM:
    """Captures the exact prompt string a debater node sends."""

    def __init__(self):
        self.prompt = None

    def invoke(self, prompt):
        self.prompt = prompt
        return SimpleNamespace(content="ok")


class RecorderChatModel:
    """Captures the rendered ChatPromptValue an analyst chain produces."""

    def __init__(self):
        self.prompt_value = None

    def bind_tools(self, tools):
        def _capture(prompt_value):
            self.prompt_value = prompt_value
            return AIMessage(content="report")

        return _capture


def _state(research_memo_context=""):
    state = {
        "messages": [HumanMessage(content="ACME")],
        "company_of_interest": "ACME",
        "asset_type": "stock",
        "instrument_context": "IC",
        "trade_date": "2026-07-09",
        "market_report": "MR",
        "sentiment_report": "SR",
        "news_report": "NR",
        "fundamentals_report": "FR",
        "investment_debate_state": {
            "history": "H", "bull_history": "BH", "bear_history": "RH",
            "current_response": "CR", "judge_decision": "", "count": 1,
        },
    }
    if research_memo_context:
        state["research_memo_context"] = research_memo_context
    return state


# --- initial state ------------------------------------------------------

def test_initial_state_existing_keys_unchanged_and_context_defaults_empty():
    state = Propagator().create_initial_state("ACME", "2026-07-09")
    assert state["research_memo_context"] == ""
    expected_existing = {
        "messages": [("human", "ACME")],
        "company_of_interest": "ACME",
        "asset_type": "stock",
        "instrument_context": "",
        "trade_date": "2026-07-09",
        "past_context": "",
        "investment_debate_state": {
            "bull_history": "", "bear_history": "", "history": "",
            "current_response": "", "judge_decision": "", "count": 0,
        },
        "risk_debate_state": {
            "aggressive_history": "", "conservative_history": "",
            "neutral_history": "", "history": "", "latest_speaker": "",
            "current_aggressive_response": "", "current_conservative_response": "",
            "current_neutral_response": "", "judge_decision": "", "count": 0,
        },
        "market_report": "", "fundamentals_report": "",
        "sentiment_report": "", "news_report": "",
    }
    for key, value in expected_existing.items():
        assert state[key] == value, key
    assert set(state) == set(expected_existing) | {"research_memo_context"}


def test_initial_state_threads_context_through():
    state = Propagator().create_initial_state(
        "ACME", "2026-07-09", research_memo_context="THE BRIEF"
    )
    assert state["research_memo_context"] == "THE BRIEF"
    assert state["past_context"] == ""   # separate channels, never conflated


# --- bull/bear golden prompts -------------------------------------------

BULL_TEMPLATE = """You are a Bull Analyst advocating for investing in the {target_label}. Your task is to build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Leverage the provided research and data to address concerns and counter bearish arguments effectively.

Key points to focus on:
- Growth Potential: Highlight the company's market opportunities, revenue projections, and scalability.
- Competitive Advantages: Emphasize factors like unique products, strong branding, or dominant market positioning.
- Positive Indicators: Use financial health, industry trends, and recent positive news as evidence.
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bear analyst's points and debating effectively rather than just listing data.

Resources available:
{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}
Use this information to deliver a compelling bull argument, refute the bear's concerns, and engage in a dynamic debate that demonstrates the strengths of the bull position.
"""

BEAR_TEMPLATE = """You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

{instrument_context}
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use this information to deliver a compelling bear argument, refute the bull's claims, and engage in a dynamic debate that demonstrates the risks and weaknesses of investing in the {target_label}.
"""


def _expected_debater_prompt(template):
    state = _state()
    return template.format(
        target_label="stock",
        instrument_context=get_instrument_context_from_state(state),
        market_research_report="MR", sentiment_report="SR", news_report="NR",
        fundamentals_label="Company fundamentals report",
        fundamentals_report="FR", history="H", current_response="CR",
    ) + get_language_instruction()


def test_bull_prompt_byte_identical_with_empty_context():
    llm = RecorderLLM()
    create_bull_researcher(llm)(_state())
    assert llm.prompt == _expected_debater_prompt(BULL_TEMPLATE)


def test_bear_prompt_byte_identical_with_empty_context():
    llm = RecorderLLM()
    create_bear_researcher(llm)(_state())
    assert llm.prompt == _expected_debater_prompt(BEAR_TEMPLATE)


def test_bull_prompt_includes_context_when_set():
    llm = RecorderLLM()
    create_bull_researcher(llm)(_state(research_memo_context="THE BRIEF"))
    assert "THE BRIEF" in llm.prompt
    assert llm.prompt != _expected_debater_prompt(BULL_TEMPLATE)


def test_bear_prompt_includes_context_when_set():
    llm = RecorderLLM()
    create_bear_researcher(llm)(_state(research_memo_context="THE BRIEF"))
    assert "THE BRIEF" in llm.prompt


# --- fundamentals analyst golden prompt ---------------------------------

FUNDAMENTALS_SYSTEM_TEMPLATE = (
    "You are a helpful AI assistant, collaborating with other assistants."
    " Use the provided tools to progress towards answering the question."
    " If you are unable to fully answer, that's OK; another assistant with different tools"
    " will help where you left off. Execute what you can to make progress."
    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
    " You have access to the following tools: {tool_names}."
    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
    "{system_message}"
)


def _expected_fundamentals_system(state):
    # NOTE: system_message is a TUPLE in the production code (trailing
    # comma) and renders via str(); reproduce exactly — do not "fix" it.
    system_message = (
        "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
        + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
        + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
        + get_language_instruction(),
    )
    return FUNDAMENTALS_SYSTEM_TEMPLATE.format(
        tool_names="get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement",
        current_date=state["trade_date"],
        instrument_context=get_instrument_context_from_state(state),
        system_message=system_message,
    )


def test_fundamentals_system_prompt_byte_identical_with_empty_context():
    rec = RecorderChatModel()
    state = _state()
    create_fundamentals_analyst(rec)(state)
    assert rec.prompt_value.messages[0].content == _expected_fundamentals_system(state)


def test_fundamentals_system_prompt_includes_context_when_set():
    rec = RecorderChatModel()
    state = _state(research_memo_context="THE BRIEF")
    create_fundamentals_analyst(rec)(state)
    system = rec.prompt_value.messages[0].content
    assert "THE BRIEF" in system
```

- [ ] **Step 2: Run — golden tests should PASS against the current code, injection tests FAIL.**

Run: `python -m pytest tests/test_research_memo_injection.py -v`
Expected: the three `byte_identical` tests and `test_initial_state_threads_context_through` FAIL only because `research_memo_context` doesn't exist yet (`create_initial_state` got an unexpected kwarg / missing state key); `includes_context` tests FAIL. **If a golden-string comparison fails for content reasons, fix the golden in the test to match current source exactly — the golden must be the pre-change truth.** (Verify the goldens first by temporarily running only the two debater byte-identical tests with the `research_memo_context` bits removed if needed.)

- [ ] **Step 3: Implement.**

`tradingagents/agents/utils/agent_states.py` — add as the last field of `AgentState`:
```python
    research_memo_context: Annotated[
        str,
        "Distilled first-cut research memo injected for graph vetting; empty on the momentum path",
    ]
```

`tradingagents/graph/propagation.py` — `create_initial_state` gains a kwarg and a state entry:
```python
    def create_initial_state(
        self,
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        instrument_context: str = "",
        research_memo_context: str = "",
    ) -> dict[str, Any]:
```
and in the returned dict, after `"past_context": past_context,`:
```python
            "research_memo_context": research_memo_context,
```
Extend the docstring with one line: `research_memo_context` is the distilled brain memo for the vetting path; empty (the default) on the momentum path, where prompts render unchanged.

`tradingagents/graph/trading_graph.py`:
```python
    def propagate(self, company_name, trade_date, asset_type: str = "stock",
                  research_memo_context: str = ""):
```
(propagate's `try` block forwards it):
```python
            return self._run_graph(
                company_name, trade_date, asset_type=asset_type,
                research_memo_context=research_memo_context,
            )
```
and:
```python
    def _run_graph(self, company_name, trade_date, asset_type: str = "stock",
                   research_memo_context: str = ""):
```
with `create_initial_state` gaining `research_memo_context=research_memo_context,`. Add one docstring line to `propagate`: ``research_memo_context`` is the research sleeve's distilled memo brief (vetting path); empty on the momentum path.

`tradingagents/agents/researchers/bull_researcher.py` — inside `bull_node`, after the `fundamentals_label` assignment:
```python
        research_memo_context = state.get("research_memo_context", "")
        research_block = (
            "Deep-research memo (a filings-based first-cut buy thesis for this "
            "company; use its cited evidence where it strengthens your case, "
            "but verify rather than assume it):\n"
            f"{research_memo_context}\n"
        ) if research_memo_context else ""
```
and in the f-string prompt change the two lines:
```
{fundamentals_label}: {fundamentals_report}
Conversation history of the debate: {history}
```
to:
```
{fundamentals_label}: {fundamentals_report}
{research_block}Conversation history of the debate: {history}
```

`tradingagents/agents/researchers/bear_researcher.py` — same pattern, with:
```python
        research_memo_context = state.get("research_memo_context", "")
        research_block = (
            "Deep-research memo (a filings-based first-cut BUY thesis for this "
            "company; your job is to stress-test it — attack its assumptions "
            "and find where it is wrong):\n"
            f"{research_memo_context}\n"
        ) if research_memo_context else ""
```
and the same `{research_block}Conversation history of the debate: {history}` splice.

`tradingagents/agents/analysts/fundamentals_analyst.py` — inside `fundamentals_analyst_node`:
```python
        research_memo_context = state.get("research_memo_context", "")
        research_block = (
            "\n\nA prior deep-research memo exists for this company (a "
            "filings-focused first-cut). Treat it as a head start on filings "
            "evidence — verify its claims with your own tools rather than "
            "repeating them:\n" + research_memo_context
        ) if research_memo_context else ""
```
Append `"{research_memo_block}"` to the END of the system template string (immediately after `"{system_message}"`), and add:
```python
        prompt = prompt.partial(research_memo_block=research_block)
```
next to the other `prompt.partial(...)` calls. With empty context the partial is `""` and the rendered prompt is byte-identical.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_research_memo_injection.py -v`
Expected: ALL PASS. Also run the neighboring graph/prompt suites:
`python -m pytest tests/test_structured_agents.py tests/test_crypto_asset_mode.py tests/test_analyst_execution.py tests/test_news_analyst_prompt.py -v`
Expected: PASS (no regressions).

- [ ] **Step 5: Commit**

```bash
git add tradingagents/agents/utils/agent_states.py tradingagents/graph/propagation.py tradingagents/graph/trading_graph.py tradingagents/agents/analysts/fundamentals_analyst.py tradingagents/agents/researchers/bull_researcher.py tradingagents/agents/researchers/bear_researcher.py tests/test_research_memo_injection.py
git commit -m "feat(graph): research_memo_context injection channel, byte-identical when empty"
```

---

### Task 6: Pipeline adapter — thread `research_context`, expose the native 5-tier rating

**Files:**
- Modify: `ops/pipeline_adapter.py`
- Test: `tests/ops/test_pipeline_adapter.py` (extend)

**Interfaces:**
- Consumes: Task 5's `TradingAgentsGraph.propagate(..., research_memo_context=...)`.
- Produces: `PipelineResult` gains `rating: str = ""` (native word: Buy/Overweight/Hold/Underweight/Sell) as the LAST dataclass field (after `raw` — do not reorder existing fields; `raw` stays third). `TradingAgentsPipelineAdapter.propagate(symbol, asof_date, research_context: str = "")`; `StubPipelineAdapter(decisions=None, ratings=None)` accepting/ignoring `research_context` and returning `ratings.get(symbol, "Hold")`. `PipelineAdapter` Protocol updated to the new signature. Task 8 consumes `result.rating` and `result.raw["final_trade_decision"]`.

- [ ] **Step 1: Read `tests/ops/test_pipeline_adapter.py`** for its fake-graph pattern, then add failing tests:

```python
def test_propagate_threads_research_context_and_exposes_native_rating(...):
    """Vetting path: the adapter forwards research_context to the graph and
    surfaces the ungraded 5-tier rating; the momentum decision still
    collapses (Overweight -> HOLD)."""
    captured = {}

    class FakeGraph:
        def propagate(self, symbol, trade_date, research_memo_context=""):
            captured["research_memo_context"] = research_memo_context
            return {"final_trade_decision": "Rating: Overweight"}, "Overweight"

    adapter = TradingAgentsPipelineAdapter()
    adapter._graph = FakeGraph()   # follow the file's existing injection pattern
    result = adapter.propagate("ACME", date(2026, 7, 9), research_context="BRIEF")
    assert captured["research_memo_context"] == "BRIEF"
    assert result.rating == "Overweight"
    assert result.decision == PipelineDecision.HOLD   # momentum collapse preserved


def test_propagate_default_context_is_empty(...):
    # momentum callers pass no context; the graph must receive ""
    ...same FakeGraph...
    adapter.propagate("ACME", date(2026, 7, 9))
    assert captured["research_memo_context"] == ""


def test_stub_adapter_accepts_and_ignores_research_context():
    stub = StubPipelineAdapter(ratings={"ACME": "Buy"})
    result = stub.propagate("ACME", date(2026, 7, 9), research_context="BRIEF")
    assert result.rating == "Buy"
    assert result.decision == PipelineDecision.HOLD


def test_stub_adapter_default_rating_is_hold():
    result = StubPipelineAdapter().propagate("X", date(2026, 7, 9))
    assert result.rating == "Hold"
```

(Adapt the graph-injection mechanics to whatever the existing tests do — if they monkeypatch `_build_graph`, do that instead of assigning `_graph`.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ops/test_pipeline_adapter.py -v`
Expected: new tests FAIL (`unexpected keyword argument 'research_context'` / no `rating`).

- [ ] **Step 3: Implement** in `ops/pipeline_adapter.py`:

`PipelineResult`:
```python
@dataclass(frozen=True)
class PipelineResult:
    symbol: str
    date: date
    decision: PipelineDecision
    raw: dict = field(default_factory=dict)
    # Native 5-tier rating word (Buy/Overweight/Hold/Underweight/Sell) from
    # the graph's signal processor. The vetting path reads this ungraded
    # rating; the momentum path keeps consuming the collapsed `decision`.
    rating: str = ""
```

Protocol:
```python
class PipelineAdapter(Protocol):
    def propagate(
        self, symbol: str, asof_date: date, research_context: str = "",
    ) -> PipelineResult: ...
```

`TradingAgentsPipelineAdapter.propagate`:
```python
    def propagate(
        self, symbol: str, asof_date: date, research_context: str = "",
    ) -> PipelineResult:
        # Bring the managed backend up lazily — only when an analysis actually
        # runs, so ticks with no candidates never load a local model.
        self._backend.ensure_up()
        graph = self._ensure_graph()
        raw, decision_text = graph.propagate(
            symbol, asof_date.isoformat(), research_memo_context=research_context,
        )
        decision = parse_decision(decision_text or "")
        raw_dict = raw if isinstance(raw, dict) else {"output": str(raw)}
        return PipelineResult(
            symbol=symbol, date=asof_date, decision=decision, raw=raw_dict,
            rating=(decision_text or "").strip(),
        )
```

`StubPipelineAdapter`:
```python
class StubPipelineAdapter:
    """In-memory adapter for tests and dry-runs. Returns fixed decisions.

    ``research_context`` is accepted and ignored; ``ratings`` maps symbols
    to a stub native rating (default "Hold") so vetting tests stay cheap.
    """

    def __init__(
        self,
        decisions: dict[str, PipelineDecision] | None = None,
        ratings: dict[str, str] | None = None,
    ):
        self._decisions = decisions or {}
        self._ratings = ratings or {}

    def propagate(
        self, symbol: str, asof_date: date, research_context: str = "",
    ) -> PipelineResult:
        decision = self._decisions.get(symbol, PipelineDecision.HOLD)
        return PipelineResult(
            symbol=symbol, date=asof_date, decision=decision, raw={},
            rating=self._ratings.get(symbol, "Hold"),
        )
```
(keep its existing `session()` context manager).

Grep before finishing: `grep -rn "PipelineResult(" ops tests` — confirm no caller constructs it with >4 positional args (the new field is keyword/defaulted, but verify).

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ops/test_pipeline_adapter.py tests/ops/scheduler tests/ops/test_integration_orchestrator.py tests/ops/test_integration_decide_once.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/pipeline_adapter.py tests/ops/test_pipeline_adapter.py
git commit -m "feat(ops): pipeline adapter threads research_context, exposes native 5-tier rating"
```

---

### Task 7: Journal events — `research_vetting_run` / `research_vetting_error`

**Files:**
- Modify: `ops/events.py`
- Test: `tests/ops/test_events_research_vetting.py` (new; mirror the style of `tests/ops/test_events_research_drain.py` — read it first)

**Interfaces:**
- Produces: `KIND_RESEARCH_VETTING_RUN = "research_vetting_run"`, `KIND_RESEARCH_VETTING_ERROR = "research_vetting_error"`, `research_vetting_run_payload(*, asof: str, vetted: int, confirmed: int, rejected: int, failed: int, still_pending: int, hit_deadline: bool) -> dict`, `research_vetting_error_payload(*, error: str) -> dict`. Both kinds registered in `BUILDERS` **and** `AUDIT_ONLY` (required by the partition test).

- [ ] **Step 1: Write the failing tests** (`tests/ops/test_events_research_vetting.py`):

```python
"""research_vetting_run / research_vetting_error event contracts."""
from ops import events


def test_vetting_kinds_are_registered_and_audit_only():
    for kind in (events.KIND_RESEARCH_VETTING_RUN,
                 events.KIND_RESEARCH_VETTING_ERROR):
        assert kind in events.BUILDERS
        assert kind in events.AUDIT_ONLY


def test_vetting_run_payload_shape():
    payload = events.research_vetting_run_payload(
        asof="2026-07-09", vetted=3, confirmed=1, rejected=1, failed=1,
        still_pending=2, hit_deadline=True,
    )
    assert payload == {
        "asof": "2026-07-09", "vetted": 3, "confirmed": 1, "rejected": 1,
        "failed": 1, "still_pending": 2, "hit_deadline": True,
    }


def test_vetting_error_payload_shape():
    assert events.research_vetting_error_payload(error="Boom: x") == {
        "error": "Boom: x",
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ops/test_events_research_vetting.py -v`
Expected: FAIL with AttributeError.

- [ ] **Step 3: Implement** in `ops/events.py`:

After `KIND_RESEARCH_DRAIN_ERROR`:
```python
KIND_RESEARCH_VETTING_RUN = "research_vetting_run"
KIND_RESEARCH_VETTING_ERROR = "research_vetting_error"
```

In `AUDIT_ONLY`, after `KIND_RESEARCH_DRAIN_ERROR,`:
```python
    KIND_RESEARCH_VETTING_RUN,
    KIND_RESEARCH_VETTING_ERROR,
```

Builders (after `research_drain_error_payload`):
```python
def research_vetting_run_payload(
    *, asof: str, vetted: int, confirmed: int, rejected: int, failed: int,
    still_pending: int, hit_deadline: bool,
) -> dict[str, Any]:
    """Overnight graph-vetting summary (funnel stage 2, mirrors the drain
    event): how the pending_vetting queue resolved and whether the stage
    stopped on the 08:00 deadline rather than emptying the queue."""
    return {
        "asof": asof, "vetted": vetted, "confirmed": confirmed,
        "rejected": rejected, "failed": failed,
        "still_pending": still_pending, "hit_deadline": hit_deadline,
    }


def research_vetting_error_payload(*, error: str) -> dict[str, Any]:
    """Vetting stage aborted (adapter/backend failure); memos stay pending."""
    return {"error": error}
```

Register both in `BUILDERS` after the drain entries:
```python
    KIND_RESEARCH_VETTING_RUN: research_vetting_run_payload,
    KIND_RESEARCH_VETTING_ERROR: research_vetting_error_payload,
```

- [ ] **Step 4: Run tests (including the partition enforcement)**

Run: `python -m pytest tests/ops/test_events_research_vetting.py tests/ops/notify/test_policy.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/events.py tests/ops/test_events_research_vetting.py
git commit -m "feat(ops): research_vetting_run/error journal event contracts"
```

---

### Task 8: Vetting orchestration — verdict/conviction mapping, falsifier extraction, merge, queue loop

**Files:**
- Create: `ops/research/vetting.py`
- Modify: `ops/research/memo_validation.py` (make the falsifier-validity check public)
- Test: `tests/ops/research/test_vetting.py` (new)

**Interfaces:**
- Consumes: `build_research_brief` (Task 4), `MemoStore.pending_vetting_memos`/`apply_vetting` (Task 2), `PipelineResult.rating`/`raw` + `adapter.propagate(symbol, date, research_context=...)` (Task 6), `VettingResult` (Task 1), `bind_structured` (existing).
- Produces:
  - `memo_validation.is_machine_checkable(falsifier) -> bool` (public; internal `_is_machine_checkable` uses become calls to it — keep behavior identical).
  - `CONFIRM_TIERS: dict[str, ConvictionTier] = {"Buy": "high", "Overweight": "medium"}` (the strictness knob).
  - `extract_risk_falsifiers(falsifier_llm, final_state: dict, *, ticker: str) -> tuple[list[Falsifier], list[str]]` (kept falsifiers, notes).
  - `vet_memo(memo, *, adapter, falsifier_llm, memo_store, vetted_by_model="") -> VetOutcome` where `VetOutcome` has `ticker: str`, `memo_id: str`, `verdict: str`, `rating: str`, `added_falsifiers: int = 0`, `notes: list[str]`.
  - `vet_pending(*, memo_store, adapter, falsifier_llm, vetted_by_model, deadline=None, should_stop=None, now=_utcnow, echo=...) -> VettingSummary` with frozen-dataclass fields `vetted: int, confirmed: int, rejected: int, failed: int, still_pending: int, hit_deadline: bool`. Task 9 consumes `vet_pending` + `VettingSummary`.

- [ ] **Step 1: Write the failing tests** (`tests/ops/research/test_vetting.py`):

```python
"""Graph vetting of brain memos: verdict from the native rating, bounded
falsifier enrichment, promote/reject persistence, deadline-boxed queue."""
from datetime import date, datetime, timedelta, timezone

import pytest

from ops.pipeline_adapter import StubPipelineAdapter
from ops.research.vetting import (
    CONFIRM_TIERS, FalsifierBatch, VettingSummary, extract_risk_falsifiers,
    vet_memo, vet_pending,
)
from tradingagents.memos.schema import EvidenceItem, Falsifier, Memo, ValueThesis
from tradingagents.memos.store import MemoStore


def _memo(ticker="ACME", **overrides):
    base = dict(
        ticker=ticker, as_of_date=date(2026, 7, 1), thesis_type="value",
        thesis="cheap for a fixable reason",
        evidence=[EvidenceItem(claim="c", source_type="filing", source_ref="0001:mdna")],
        value_block=ValueThesis(
            why_cheap="segment decline", change_trigger="new CEO",
            normalized_earnings_view="2x", quality_assessment="net cash",
        ),
        conviction_tier="starter", entry_price_ref=10.0,
        price_target_low=15.0, price_target_high=20.0,
        expected_holding_months=12, must_be_true=["m"],
        falsifiers=[Falsifier(description="margin collapse",
                              check_type="fundamental", metric="gross_margin_pct",
                              operator="<", threshold=30.0)],
        status="pending_vetting",
    )
    base.update(overrides)
    return Memo(**base)


@pytest.fixture
def store(tmp_path):
    return MemoStore(tmp_path / "memos.sqlite")


class NoFalsifierLLM:
    """with_structured_output unsupported -> extraction skipped cleanly."""

    def with_structured_output(self, schema):
        raise NotImplementedError


class FixedFalsifierLLM:
    """Structured extraction returns a fixed batch."""

    def __init__(self, items):
        self._items = items

    def with_structured_output(self, schema):
        items = self._items

        class _Runner:
            def invoke(self, prompt):
                return FalsifierBatch(items=items)

        return _Runner()


class BoomFalsifierLLM:
    def with_structured_output(self, schema):
        class _Runner:
            def invoke(self, prompt):
                raise RuntimeError("boom")

        return _Runner()


# --- verdict + conviction mapping (native rating) ------------------------

def test_confirm_map_is_the_spec_table():
    assert CONFIRM_TIERS == {"Buy": "high", "Overweight": "medium"}


@pytest.mark.parametrize("rating,tier", [("Buy", "high"), ("Overweight", "medium")])
def test_confirm_promotes_with_mapped_conviction(store, rating, tier):
    memo = _memo()
    store.save(memo)
    adapter = StubPipelineAdapter(ratings={"ACME": rating})
    outcome = vet_memo(memo, adapter=adapter, falsifier_llm=NoFalsifierLLM(),
                       memo_store=store, vetted_by_model="graph:ds4")
    assert outcome.verdict == "confirm"
    got = store.get(memo.memo_id)
    assert got.status == "open"
    assert got.conviction_tier == tier
    assert got.vetting.verdict == "confirm"
    assert got.vetting.rating == rating
    assert got.vetting.conviction_before == "starter"
    assert got.vetting.conviction_after == tier
    assert got.vetting.vetted_by_model == "graph:ds4"


@pytest.mark.parametrize("rating", ["Hold", "Underweight", "Sell", "", "garbage"])
def test_non_buy_ratings_reject(store, rating):
    memo = _memo()
    store.save(memo)
    adapter = StubPipelineAdapter(ratings={"ACME": rating})
    outcome = vet_memo(memo, adapter=adapter, falsifier_llm=NoFalsifierLLM(),
                       memo_store=store)
    assert outcome.verdict == "reject"
    got = store.get(memo.memo_id)
    assert got.status == "rejected"
    assert got.conviction_tier == "starter"       # untouched on reject
    assert got.vetting.verdict == "reject"
    assert got.vetting.conviction_after is None
    assert len(got.falsifiers) == 1               # nothing appended on reject
    assert store.open_memos() == []


def test_vet_memo_passes_brief_and_asof_to_adapter(store):
    captured = {}

    class SpyAdapter:
        def propagate(self, symbol, asof_date, research_context=""):
            captured["symbol"] = symbol
            captured["asof"] = asof_date
            captured["context"] = research_context
            return StubPipelineAdapter(ratings={symbol: "Hold"}).propagate(
                symbol, asof_date, research_context)

    memo = _memo()
    store.save(memo)
    vet_memo(memo, adapter=SpyAdapter(), falsifier_llm=NoFalsifierLLM(),
             memo_store=store)
    assert captured["symbol"] == "ACME"
    assert captured["asof"] == date(2026, 7, 1)   # memo.as_of_date
    assert "RESEARCH MEMO BRIEF" in captured["context"]
    assert "cheap for a fixable reason" in captured["context"]


# --- risk-falsifier extraction (option B, gate-validated) -----------------

def test_extraction_keeps_only_machine_checkable():
    good = Falsifier(description="drawdown", check_type="price",
                     metric="drawdown_from_cost_pct", operator="<", threshold=-25.0)
    prose = Falsifier(description="vibes deteriorate", check_type="event")
    partial = Falsifier(description="margin", check_type="fundamental",
                        metric="gross_margin_pct")   # no operator/threshold
    kept, notes = extract_risk_falsifiers(
        FixedFalsifierLLM([good, prose, partial]),
        {"risk_debate_state": {"history": "H", "judge_decision": "J"}},
        ticker="ACME",
    )
    assert kept == [good]
    assert any("2" in n for n in notes)   # 2 dropped, noted


def test_extraction_failure_returns_empty_with_note():
    kept, notes = extract_risk_falsifiers(
        BoomFalsifierLLM(),
        {"risk_debate_state": {"history": "H", "judge_decision": "J"}},
        ticker="ACME",
    )
    assert kept == []
    assert notes and "failed" in notes[0]


def test_extraction_skips_on_empty_debate():
    kept, notes = extract_risk_falsifiers(
        FixedFalsifierLLM([]), {"risk_debate_state": {}}, ticker="ACME",
    )
    assert kept == []
    assert notes


def test_confirm_appends_validated_falsifiers_with_indices(store):
    memo = _memo()
    store.save(memo)
    good = Falsifier(description="drawdown", check_type="price",
                     metric="drawdown_from_cost_pct", operator="<", threshold=-25.0)
    prose = Falsifier(description="vibes", check_type="event")
    adapter = StubPipelineAdapter(ratings={"ACME": "Buy"})
    outcome = vet_memo(memo, adapter=adapter,
                       falsifier_llm=FixedFalsifierLLM([good, prose]),
                       memo_store=store)
    assert outcome.verdict == "confirm"
    assert outcome.added_falsifiers == 1
    got = store.get(memo.memo_id)
    assert len(got.falsifiers) == 2
    assert got.falsifiers[1].metric == "drawdown_from_cost_pct"
    assert got.vetting.added_falsifier_indices == [1]


def test_extraction_failure_never_blocks_a_confirm(store):
    """Spec: verdict/conviction come only from the rating; a failed
    enrichment call confirms with the brain's falsifiers alone."""
    memo = _memo()
    store.save(memo)
    adapter = StubPipelineAdapter(ratings={"ACME": "Buy"})
    outcome = vet_memo(memo, adapter=adapter, falsifier_llm=BoomFalsifierLLM(),
                       memo_store=store)
    assert outcome.verdict == "confirm"
    got = store.get(memo.memo_id)
    assert got.status == "open"
    assert len(got.falsifiers) == 1
    assert got.vetting.added_falsifier_indices == []
    assert "falsifier extraction failed" in got.vetting.rationale


# --- queue loop -----------------------------------------------------------

def _utc(h=1):
    return datetime(2026, 7, 9, h, 0, tzinfo=timezone.utc)


def test_vet_pending_processes_oldest_first_and_counts(store):
    older = _memo(ticker="AAA",
                  created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    newer = _memo(ticker="BBB",
                  created_at=datetime(2026, 7, 5, tzinfo=timezone.utc))
    store.save(newer)
    store.save(older)
    order = []

    class OrderSpy(StubPipelineAdapter):
        def propagate(self, symbol, asof_date, research_context=""):
            order.append(symbol)
            return super().propagate(symbol, asof_date, research_context)

    summary = vet_pending(
        memo_store=store,
        adapter=OrderSpy(ratings={"AAA": "Buy", "BBB": "Sell"}),
        falsifier_llm=NoFalsifierLLM(), vetted_by_model="m",
    )
    assert order == ["AAA", "BBB"]
    assert summary == VettingSummary(vetted=2, confirmed=1, rejected=1,
                                     failed=0, still_pending=0, hit_deadline=False)


def test_vet_pending_stops_at_deadline_between_memos(store):
    for t in ("AAA", "BBB"):
        store.save(_memo(ticker=t))
    clock = iter([_utc(1), _utc(9)])   # first check passes, second hits deadline
    summary = vet_pending(
        memo_store=store, adapter=StubPipelineAdapter(ratings={"AAA": "Buy"}),
        falsifier_llm=NoFalsifierLLM(), vetted_by_model="m",
        deadline=_utc(8), now=lambda: next(clock),
    )
    assert summary.vetted == 1
    assert summary.hit_deadline is True
    assert summary.still_pending == 1


def test_vet_pending_honors_should_stop(store):
    store.save(_memo(ticker="AAA"))
    summary = vet_pending(
        memo_store=store, adapter=StubPipelineAdapter(),
        falsifier_llm=NoFalsifierLLM(), vetted_by_model="m",
        should_stop=lambda: True,
    )
    assert summary.vetted == 0
    assert summary.still_pending == 1


def test_vet_pending_failure_leaves_memo_pending_and_continues(store):
    for t in ("AAA", "BBB"):
        store.save(_memo(ticker=t))

    class FlakyAdapter(StubPipelineAdapter):
        def propagate(self, symbol, asof_date, research_context=""):
            if symbol == "AAA":
                raise RuntimeError("graph exploded")
            return super().propagate(symbol, asof_date, research_context)

    echoes = []
    summary = vet_pending(
        memo_store=store, adapter=FlakyAdapter(ratings={"BBB": "Buy"}),
        falsifier_llm=NoFalsifierLLM(), vetted_by_model="m",
        echo=echoes.append,
    )
    assert summary.failed == 1
    assert summary.confirmed == 1
    assert summary.still_pending == 1          # AAA retried next night
    assert [m.ticker for m in store.pending_vetting_memos()] == ["AAA"]
    assert any("AAA" in e and "FAILED" in e for e in echoes)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/ops/research/test_vetting.py -v`
Expected: FAIL with ModuleNotFoundError.

- [ ] **Step 3a: Make the falsifier gate public.** In `ops/research/memo_validation.py` rename `_is_machine_checkable` to `is_machine_checkable` (keep a module-level alias `_is_machine_checkable = is_machine_checkable` ONLY if grep shows external users of the underscore name — check `grep -rn "_is_machine_checkable" ops tests`), update the internal `validate_memo` call, and update the docstring line to note it is shared with the vetting stage's extraction gate.

- [ ] **Step 3b: Implement** `ops/research/vetting.py`:

```python
"""Graph vetting of brain-authored memos (funnel stage 2).

The brain researches; the graph decides. Each pending_vetting memo (a
brain-buy by construction) is distilled into a deterministic brief,
injected into the multi-agent graph as ``research_memo_context``, and
adjudicated from the graph's NATIVE 5-tier rating — no extra LLM call and
no agent-prompt change decide the verdict:

    Buy        -> confirm, conviction high
    Overweight -> confirm, conviction medium
    anything else (Hold/Underweight/Sell/unparseable) -> reject

The mapping lives here, in code — no agent ever learns the
starter/medium/high taxonomy. One additional bounded structured call
extracts machine-checkable falsifiers from the risk debate; each candidate
must pass the same mechanical validity gate brain falsifiers face
(metric+operator+threshold), so the debate can only ADD monitorable exit
conditions. Extraction is additive: its failure never blocks a confirm and
never stores garbage.

Mirrors ops/research/drain.py's deadline/shutdown-boxed loop: stop
conditions are checked BEFORE each memo so a graph run in flight always
finishes; a per-memo failure leaves the memo pending_vetting (retried next
night) and never raises out of the loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from ops.research.memo_brief import build_research_brief
from ops.research.memo_validation import is_machine_checkable
from tradingagents.agents.utils.structured import bind_structured
from tradingagents.memos.schema import (
    ConvictionTier,
    Falsifier,
    Memo,
    VettingResult,
)

# Native graph rating -> conviction tier on confirm. Ratings absent from
# this map reject. THE strictness knob: a stricter policy would drop
# "Overweight" (spec default is the two-row table).
CONFIRM_TIERS: dict[str, ConvictionTier] = {"Buy": "high", "Overweight": "medium"}

MAX_DEBATE_CHARS = 12000
MAX_RATIONALE_CHARS = 2000


class FalsifierBatch(BaseModel):
    items: list[Falsifier] = Field(default_factory=list)


FALSIFIER_PROMPT = """\
You just watched a risk-management debate about buying {ticker}. Extract the
risk team's concerns as MACHINE-CHECKABLE exit conditions (falsifiers) for
the position. Rules:
- Each item MUST set metric, operator, AND threshold (metric examples:
  gross_margin_pct, revenue_yoy_pct, net_debt_to_ebitda,
  drawdown_from_cost_pct). Items without all three are discarded.
- check_type: "fundamental" for quarterly financials, "price" for market
  data, "event" for filing/deal state.
- Only conditions actually argued in the debate below — no inventions.
- At most 3 items; return an empty list if the debate raised nothing
  machine-checkable.

Risk debate:
{history}

Risk judge's decision:
{judge}
"""


@dataclass
class VetOutcome:
    ticker: str
    memo_id: str
    verdict: str  # "confirm" | "reject"
    rating: str
    added_falsifiers: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VettingSummary:
    vetted: int
    confirmed: int
    rejected: int
    failed: int
    still_pending: int
    hit_deadline: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def extract_risk_falsifiers(
    falsifier_llm, final_state: dict, *, ticker: str,
) -> tuple[list[Falsifier], list[str]]:
    """One bounded structured pass over the risk debate -> validated falsifiers.

    Returns (kept, notes). Never raises: any failure returns ([], [note]) so
    the caller confirms with the brain's falsifiers alone.
    """
    structured = bind_structured(falsifier_llm, FalsifierBatch, "research-vetting-falsifiers")
    if structured is None:
        return [], ["falsifier extraction skipped: no structured-output support"]
    risk = final_state.get("risk_debate_state") or {}
    history = (risk.get("history") or "")[:MAX_DEBATE_CHARS]
    judge = (risk.get("judge_decision") or "")[:MAX_RATIONALE_CHARS]
    if not history and not judge:
        return [], ["falsifier extraction skipped: empty risk debate"]
    try:
        batch = structured.invoke(
            FALSIFIER_PROMPT.format(ticker=ticker, history=history, judge=judge)
        )
    except Exception as exc:  # noqa: BLE001 - enrichment must never block a confirm
        return [], [f"falsifier extraction failed: {type(exc).__name__}: {exc}"]
    if batch is None:
        return [], ["falsifier extraction returned no structured output"]
    kept = [f for f in batch.items if is_machine_checkable(f)]
    notes = []
    dropped = len(batch.items) - len(kept)
    if dropped:
        notes.append(f"dropped {dropped} non-machine-checkable falsifier(s)")
    return kept, notes


def vet_memo(
    memo: Memo, *, adapter, falsifier_llm, memo_store, vetted_by_model: str = "",
) -> VetOutcome:
    """Run the graph over one memo and persist the adjudication."""
    brief = build_research_brief(memo)
    result = adapter.propagate(memo.ticker, memo.as_of_date, research_context=brief)
    rating = (result.rating or "").strip()
    rationale = str(result.raw.get("final_trade_decision", ""))[:MAX_RATIONALE_CHARS]
    tier = CONFIRM_TIERS.get(rating)
    outcome = VetOutcome(
        ticker=memo.ticker, memo_id=memo.memo_id, verdict="reject", rating=rating,
    )

    if tier is None:
        memo.status = "rejected"
        memo.vetting = VettingResult(
            verdict="reject", rating=rating,
            conviction_before=memo.conviction_tier, conviction_after=None,
            rationale=rationale, vetted_by_model=vetted_by_model,
        )
        memo_store.apply_vetting(memo)
        return outcome

    added, notes = extract_risk_falsifiers(
        falsifier_llm, result.raw, ticker=memo.ticker,
    )
    if notes:
        rationale = (rationale + "\n[vetting] " + "; ".join(notes))[:MAX_RATIONALE_CHARS + 500]
    indices = list(range(len(memo.falsifiers), len(memo.falsifiers) + len(added)))
    conviction_before = memo.conviction_tier
    memo.falsifiers = memo.falsifiers + added
    memo.conviction_tier = tier
    memo.status = "open"
    memo.vetting = VettingResult(
        verdict="confirm", rating=rating,
        conviction_before=conviction_before, conviction_after=tier,
        added_falsifier_indices=indices, rationale=rationale,
        vetted_by_model=vetted_by_model,
    )
    memo_store.apply_vetting(memo)
    outcome.verdict = "confirm"
    outcome.added_falsifiers = len(added)
    outcome.notes = notes
    return outcome


def vet_pending(
    *,
    memo_store,
    adapter,
    falsifier_llm,
    vetted_by_model: str,
    deadline: datetime | None = None,
    should_stop: Callable[[], bool] | None = None,
    now: Callable[[], datetime] = _utcnow,
    echo: Callable[[str], None] = lambda msg: None,
) -> VettingSummary:
    """Vet the pending_vetting queue oldest-first until deadline/stop/empty."""
    memos = memo_store.pending_vetting_memos()
    vetted = confirmed = rejected = failed = 0
    hit_deadline = False
    for memo in memos:
        if should_stop is not None and should_stop():
            break
        if deadline is not None and now() >= deadline:
            hit_deadline = True
            break
        try:
            outcome = vet_memo(
                memo, adapter=adapter, falsifier_llm=falsifier_llm,
                memo_store=memo_store, vetted_by_model=vetted_by_model,
            )
        except Exception as exc:  # noqa: BLE001 - one bad name must not strand the queue
            failed += 1
            echo(f"{memo.ticker}: FAILED ({type(exc).__name__}: {exc})")
            continue
        vetted += 1
        if outcome.verdict == "confirm":
            confirmed += 1
        else:
            rejected += 1
        echo(
            f"{outcome.ticker}: {outcome.verdict} (rating {outcome.rating}; "
            f"+{outcome.added_falsifiers} falsifiers)"
        )
    return VettingSummary(
        vetted=vetted, confirmed=confirmed, rejected=rejected, failed=failed,
        still_pending=len(memo_store.pending_vetting_memos()),
        hit_deadline=hit_deadline,
    )
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ops/research/test_vetting.py tests/ops/research/test_memo_validation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ops/research/vetting.py ops/research/memo_validation.py tests/ops/research/test_vetting.py
git commit -m "feat(research): graph vetting — native-rating adjudication + validated falsifier enrichment"
```

---

### Task 9: Scheduling — vetting stage inside the overnight window

**Files:**
- Modify: `ops/main.py` (`_research_overnight_tick` + new `_research_vetting_stage` helper)
- Test: `tests/ops/test_main.py` (extend; update the existing overnight tests)

**Interfaces:**
- Consumes: `vet_pending`/`VettingSummary` (Task 8), vetting events (Task 7), `MemoStore.pending_vetting_memos` (Task 2), `TradingAgentsPipelineAdapter` (Task 6).
- Produces: the overnight tick runs drain then vetting under one deadline and ONE backend `ensure_up`/`shutdown` bracket; skips ds4 entirely when both queues are empty; vetting failures record `research_vetting_error` without killing the drain event or raising; a vet-only night (empty drain queue, non-empty vetting queue) still runs. New injectable kwarg `vet_adapter_factory` for tests.

- [ ] **Step 1: Read the existing overnight tests** (`tests/ops/test_main.py::test_overnight_tick_*`, lines ~659–830) to reuse their fixture/monkeypatch pattern (they stub `ops.research.store.ScreenStore`, `ops.research.run.run_screen`, `ops.research.drain.drain_pending`, `ops.research.models.build_stage_llm`, `main_mod.build_managed_backend`, etc.). Then add/adjust failing tests:

```python
def test_overnight_tick_vets_after_drain_and_records_event(monkeypatch, tmp_path):
    """Vetting runs after the drain, inside the same backend bracket, and
    records research_vetting_run."""
    # arrange like test_overnight_tick_screens_when_due_then_drains, plus:
    # - config.memo_store_path -> tmp sqlite seeded with one pending_vetting memo
    # - monkeypatch ops.research.vetting.vet_pending -> returns
    #   VettingSummary(vetted=1, confirmed=1, rejected=0, failed=0,
    #                  still_pending=0, hit_deadline=False), capturing kwargs
    # - vet_adapter_factory=lambda backend: sentinel_adapter
    # act: main_mod._research_overnight_tick(journal, config, vet_adapter_factory=...)
    # assert: vet_pending called once with adapter=sentinel_adapter and the
    #   same deadline the drain got; journal has research_drain_run AND
    #   research_vetting_run events; backend.shutdown called exactly once.


def test_overnight_tick_vet_only_night_runs_vetting_without_drain(monkeypatch, tmp_path):
    """Empty screen queue + non-empty vetting queue: drain records its zero
    event, vetting still runs (backlog night)."""
    # pending_hits -> [], memo store seeded with a pending_vetting memo
    # assert vet_pending called; research_drain_run zero event present;
    # research_vetting_run present.


def test_overnight_tick_both_queues_empty_skips_backend(monkeypatch, tmp_path):
    """Extends the existing empty-queue-skips-backend guarantee to the
    vetting queue: ds4 must not spin when there is nothing to do."""
    # pending_hits -> [], memo store empty
    # backend factory monkeypatched to raise if called (or ensure_up spy)
    # assert zero drain event recorded, no vetting event, backend untouched.


def test_overnight_tick_vetting_error_recorded_not_raised(monkeypatch, tmp_path):
    """A vetting-stage failure records research_vetting_error, keeps the
    drain's success event, and still shuts the backend down."""
    # vet_pending raises RuntimeError("kaboom")
    # assert tick does not raise; research_vetting_error event present with
    # "RuntimeError: kaboom"; backend.shutdown called.
```

Write these as real tests following the file's existing fake patterns (FakeBackend with `ensure_up`/`shutdown` counters already exists there or is trivial to add). Update `test_overnight_tick_empty_queue_skips_backend` so its config points `memo_store_path` at an empty tmp sqlite (it now must be empty on BOTH queues to skip), and update `test_overnight_tick_screens_when_due_then_drains` / `test_overnight_tick_skips_screen_when_recent` to point `memo_store_path` at a tmp path (empty vetting queue ⇒ vetting stage no-ops silently).

- [ ] **Step 2: Run to verify the new tests fail**

Run: `python -m pytest tests/ops/test_main.py -v -k overnight`
Expected: new tests FAIL (`unexpected keyword argument 'vet_adapter_factory'` / missing events).

- [ ] **Step 3: Implement.** In `ops/main.py`, replace `_research_overnight_tick` with the two-stage version and add the helper:

```python
def _research_overnight_tick(
    journal: Journal, config, *, now=None, should_stop=None, vet_adapter_factory=None,
) -> None:
    """Nightly 00:00 job, two stages under one deadline and one ds4 bracket:
    (1) screen if >= research_screen_interval_days, then drain pending screen
    hits into brain memos; (2) graph-vet the pending_vetting queue (brain
    buys) oldest-first. Both stages stop at the same local deadline hour /
    on shutdown; whatever isn't vetted tonight stays pending_vetting and
    carries to the next night. Scheduler-safe: a drain failure records
    research_drain_error, a vetting failure records research_vetting_error
    (see _research_vetting_stage); neither raises.

    No has_event_today gate here (unlike the sibling research ticks): the
    3-day screen-due check plus the two queue states already make re-firing
    idempotent/safe — a second run same night either finds both queues empty
    (no-op, skipping ds4 entirely) or correctly resumes whatever is pending.
    """
    screened_this_run = False
    try:
        from ops.research.store import ScreenStore
        from tradingagents.memos.store import MemoStore

        store = ScreenStore(config.screen_store_path)
        last = store.last_run()
        due = last is None or _days_since_iso(last["created_at"]) >= config.research_screen_interval_days
        if due:
            from ops.research.run import run_screen

            run_screen(config=config, asof=date.today())
            screened_this_run = True

        memo_store = MemoStore(config.memo_store_path)
        pending = store.pending_hits()
        if not pending and not memo_store.pending_vetting_memos():
            # Nothing to drain OR vet — skip waking the 86 GB ds4 model
            # entirely. This is the common case (~2 of 3 nights).
            journal.record_event(
                events.KIND_RESEARCH_DRAIN_RUN,
                events.research_drain_run_payload(
                    asof=date.today().isoformat(), screened_this_run=screened_this_run,
                    researched=0, failed=0, still_pending=0, hit_deadline=False,
                ),
            )
            return

        deadline = _drain_deadline(config.research_drain_deadline_hour)
        stop = should_stop or _shutdown_event.is_set
        tick_now = now or (lambda: datetime.now(deadline.tzinfo))
        backend = build_managed_backend(load_managed_backend_config())
        try:
            if pending:
                from tradingagents.dataflows import edgar
                edgar.get_user_agent()  # fail fast before spinning ds4

                from ops.research.drain import drain_pending
                from ops.research.models import build_stage_llm

                evidence_llm = build_stage_llm(config.research_evidence_model)
                thesis_llm = build_stage_llm(config.research_thesis_model)
                backend.ensure_up()
                summary = drain_pending(
                    store=store, memo_store=memo_store,
                    evidence_llm=evidence_llm, thesis_llm=thesis_llm,
                    thesis_model_spec=config.research_thesis_model,
                    deadline=deadline, should_stop=stop, now=tick_now,
                )
                journal.record_event(
                    events.KIND_RESEARCH_DRAIN_RUN,
                    events.research_drain_run_payload(
                        asof=date.today().isoformat(), screened_this_run=screened_this_run,
                        researched=summary.researched, failed=summary.failed,
                        still_pending=summary.still_pending, hit_deadline=summary.hit_deadline,
                    ),
                )
            else:
                journal.record_event(
                    events.KIND_RESEARCH_DRAIN_RUN,
                    events.research_drain_run_payload(
                        asof=date.today().isoformat(), screened_this_run=screened_this_run,
                        researched=0, failed=0, still_pending=0, hit_deadline=False,
                    ),
                )
            _research_vetting_stage(
                journal, config, memo_store=memo_store, backend=backend,
                deadline=deadline, should_stop=stop, now=tick_now,
                adapter_factory=vet_adapter_factory,
            )
        finally:
            backend.shutdown()
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        journal.record_event(
            events.KIND_RESEARCH_DRAIN_ERROR,
            events.research_drain_error_payload(
                error=f"{type(exc).__name__}: {exc}",
            ),
        )


def _research_vetting_stage(
    journal: Journal, config, *, memo_store, backend, deadline, should_stop,
    now, adapter_factory=None,
) -> None:
    """Stage 2 of the overnight tick: graph-vet the pending_vetting queue.

    Scheduler-safe and drain-independent: any failure records
    research_vetting_error and returns — the drain's success event is
    already journaled, memos stay pending_vetting for the next night, and
    the caller's finally still tears ds4 down. The graph adapter shares the
    tick's managed backend; its lazy ensure_up means a vet-only night spins
    ds4 only when a memo actually runs.
    """
    try:
        if not memo_store.pending_vetting_memos():
            return
        from ops.research.models import build_stage_llm
        from ops.research.vetting import vet_pending
        from tradingagents.default_config import DEFAULT_CONFIG

        if adapter_factory is None:
            from ops.pipeline_adapter import TradingAgentsPipelineAdapter

            adapter = TradingAgentsPipelineAdapter(backend=backend)
        else:
            adapter = adapter_factory(backend)
        summary = vet_pending(
            memo_store=memo_store, adapter=adapter,
            falsifier_llm=build_stage_llm(config.research_thesis_model),
            vetted_by_model=f"{DEFAULT_CONFIG['llm_provider']}:{DEFAULT_CONFIG['deep_think_llm']}",
            deadline=deadline, should_stop=should_stop, now=now,
        )
        journal.record_event(
            events.KIND_RESEARCH_VETTING_RUN,
            events.research_vetting_run_payload(
                asof=date.today().isoformat(), vetted=summary.vetted,
                confirmed=summary.confirmed, rejected=summary.rejected,
                failed=summary.failed, still_pending=summary.still_pending,
                hit_deadline=summary.hit_deadline,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        journal.record_event(
            events.KIND_RESEARCH_VETTING_ERROR,
            events.research_vetting_error_payload(
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
```

Note the behavior changes vs today, which the tests must cover: (a) `ensure_up` now happens only in the `pending` branch or lazily via the adapter; (b) the zero drain event is recorded even on a vet-only night; (c) `_drain_deadline`'s docstring guarantee (ds4 freed before 09:00) now covers both stages via the shared `deadline` + the `finally` shutdown.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/ops/test_main.py -v`
Expected: PASS (except the known `test_daily_overview_tick_writes_file_and_records_gate_event` timezone flake — verify it fails identically on the base commit if it shows up).

- [ ] **Step 5: Commit**

```bash
git add ops/main.py tests/ops/test_main.py
git commit -m "feat(ops): overnight graph-vetting stage after the brain drain (shared deadline + ds4 bracket)"
```

---

### Task 10: Whole-suite verification

**Files:** none (verification only; fix regressions if found).

- [ ] **Step 1: Run the ops suite**

Run: `python -m pytest tests/ops -q`
Expected: all green except (possibly) the known `test_daily_overview_tick_writes_file_and_records_gate_event` timezone flake.

- [ ] **Step 2: Run the root-level suites touched by this work**

Run: `python -m pytest tests/test_memo_store.py tests/test_research_memo_injection.py tests/test_structured_agents.py tests/test_crypto_asset_mode.py tests/test_analyst_execution.py tests/test_signal_processing.py tests/test_memory_log.py -q`
Expected: PASS.

- [ ] **Step 3: Grep sanity checks**

```bash
grep -rn "status=\"open\"" ops/research/brain.py        # expect: no hits
grep -rn "past_context" ops/research/vetting.py         # expect: no hits
grep -rn "research_memo_context" tradingagents/graph/propagation.py tradingagents/graph/trading_graph.py  # threaded
```

- [ ] **Step 4: Commit anything outstanding, then hand off to the finishing skill** (final whole-branch review + PR).

---

## Self-review notes (spec coverage)

- §1 funnel + §2 schema/lifecycle → Tasks 1–3. §3 brief → Task 4. §4 injection/adapter → Tasks 5–6. §5 adjudication → Task 8. §6 scheduling + events → Tasks 7, 9. §7 downstream unchanged → no code (pinned by Task 2's gate test + untouched `_entry_pass`). Error handling § → Tasks 8–9 tests. Testing § maps 1:1 onto the task test lists. Rollout §: one branch (global constraint); AEO grandfathering is a no-op in code (existing `open` rows untouched) — note it in the PR body.
- Deliberately out of scope (spec silence, YAGNI): no CLI `ops research vet` command; no attempt-cap on repeated per-name vetting failures (spec lists it as a possible later change); no migration of existing memos.
