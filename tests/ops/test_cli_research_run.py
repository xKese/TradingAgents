"""Unit tests for `ops research run` (LLMs, stores, and backend all faked)."""

from datetime import date
from decimal import Decimal

import pytest
from click.testing import CliRunner

import ops.cli as cli_mod
from ops.research.brain import ResearchOutcome

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_SCREEN_STORE_PATH", str(tmp_path / "screen.sqlite"))
    monkeypatch.setenv("OPS_MEMO_STORE_PATH", str(tmp_path / "memos.sqlite"))
    monkeypatch.delenv("OPS_LLM_MANAGED_BACKEND", raising=False)
    # Hermetic regardless of what the real environment has: `research run`
    # now fails fast on a missing SEC_EDGAR_USER_AGENT (see
    # test_missing_sec_edgar_user_agent_fails_fast_without_marking below),
    # so every other test in this file needs one set to reach its behavior.
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "Test Suite test@example.com")
    # The command imports these lazily (repo convention: heavy imports live
    # in command bodies), so patch the SOURCE modules, not ops.cli.
    monkeypatch.setattr("ops.research.models.build_stage_llm", lambda spec: f"llm:{spec}")
    return tmp_path


def _seed_hits(tmp_path, symbols):
    from ops.research.screener import Bar, ScreenResult
    from ops.research.store import ScreenStore

    store = ScreenStore(tmp_path / "screen.sqlite")
    results = [
        ScreenResult(
            symbol=s, asof=date(2026, 7, 4), passed=True, cheap=True, quality=True,
            valuation_bars=(Bar("fcf_yield", True, "ok"),),
            quality_bars=(Bar("roic_5y", True, "ok"),),
            triggers=(), market_cap=Decimal("450000000"), ev_ebit=Decimal("6"),
        )
        for s in symbols
    ]
    store.record_run(asof=date(2026, 7, 4), universe_size=9, results=results)
    return store


def test_no_pending_hits_exits_zero(env):
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["research", "run"])
    assert result.exit_code == 0
    assert "no pending hits" in result.output


def test_researches_marks_and_summarizes(env, monkeypatch):
    store = _seed_hits(env, ["AAA", "BBB", "CCC", "DDD"])

    def fake_research(hit, **kw):
        status = "failed" if hit["symbol"] == "BBB" else "researched"
        return ResearchOutcome(
            symbol=hit["symbol"], hit_id=hit["id"], status=status,
            memo_id="m-" + hit["symbol"] if status == "researched" else None,
            recommendation="buy" if status == "researched" else None,
            errors=["no machine-checkable falsifier"] if status == "failed" else [],
        )

    monkeypatch.setattr("ops.research.brain.research_hit", fake_research)
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["research", "run", "--max-names", "3"])
    assert result.exit_code == 0, result.output
    statuses = {h["symbol"]: h["status"] for h in _all_hits(store)}
    assert statuses == {
        "AAA": "researched", "BBB": "failed", "CCC": "researched", "DDD": "pending",
    }
    assert "2 researched, 1 failed" in result.output


def test_all_failed_exits_one(env, monkeypatch):
    _seed_hits(env, ["AAA"])
    monkeypatch.setattr(
        "ops.research.brain.research_hit",
        lambda hit, **kw: ResearchOutcome(
            symbol=hit["symbol"], hit_id=hit["id"], status="failed",
            errors=["insufficient cited evidence"],
        ),
    )
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["research", "run"])
    assert result.exit_code == 1


def test_unexpected_exception_marks_failed_and_continues(env, monkeypatch):
    store = _seed_hits(env, ["AAA", "BBB"])
    calls = {"n": 0}

    def flaky(hit, **kw):
        calls["n"] += 1
        if hit["symbol"] == "AAA":
            raise RuntimeError("backend hiccup")
        return ResearchOutcome(
            symbol=hit["symbol"], hit_id=hit["id"], status="researched",
            memo_id="m-BBB", recommendation="pass",
        )

    monkeypatch.setattr("ops.research.brain.research_hit", flaky)
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["research", "run"])
    assert result.exit_code == 0, result.output
    statuses = {h["symbol"]: h["status"] for h in _all_hits(store)}
    assert statuses == {"AAA": "failed", "BBB": "researched"}


def test_missing_sec_edgar_user_agent_fails_fast_without_marking(env, monkeypatch):
    store = _seed_hits(env, ["AAA"])
    # Override the env fixture's SEC_EDGAR_USER_AGENT: this test exercises
    # the unset case specifically, hermetic to whatever the real shell has.
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)

    def must_not_be_called(hit, **kw):
        raise AssertionError("research_hit must not be called when EDGAR is unconfigured")

    monkeypatch.setattr("ops.research.brain.research_hit", must_not_be_called)
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["research", "run"])
    assert result.exit_code != 0
    statuses = {h["symbol"]: h["status"] for h in _all_hits(store)}
    assert statuses == {"AAA": "pending"}


def _all_hits(store):
    with store._connect() as conn:
        rows = conn.execute("SELECT symbol, status FROM screen_hits ORDER BY id").fetchall()
    return [{"symbol": r["symbol"], "status": r["status"]} for r in rows]


def test_notify_sends_summary_after_batch(env, monkeypatch):
    _seed_hits(env, ["AAA"])
    monkeypatch.setattr(
        "ops.research.brain.research_hit",
        lambda hit, **kw: ResearchOutcome(
            symbol=hit["symbol"], hit_id=hit["id"], status="researched",
            memo_id="m-AAA", recommendation="buy",
        ),
    )
    sent = []

    class FakeTransport:
        def send(self, message):
            sent.append(message)

    monkeypatch.setattr("ops.notify.push.build_push_transport", lambda cfg: FakeTransport())
    result = CliRunner().invoke(cli_mod.cli, ["research", "run", "--notify"])
    assert result.exit_code == 0, result.output
    assert len(sent) == 1
    assert "1 researched" in sent[0].body


def test_notify_silent_when_no_pending_hits(env, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "ops.notify.push.build_push_transport",
        lambda cfg: type("T", (), {"send": lambda self, m: sent.append(m)})(),
    )
    result = CliRunner().invoke(cli_mod.cli, ["research", "run", "--notify"])
    assert result.exit_code == 0
    assert sent == []


def test_notify_sends_high_urgency_on_batch_failure(env, monkeypatch):
    _seed_hits(env, ["AAA"])

    def boom(hit, **kw):
        raise RuntimeError("edgar on fire")

    monkeypatch.setattr("ops.research.brain.research_hit", boom)
    monkeypatch.setattr(
        "ops.llm_backend.build_managed_backend",
        lambda cfg: (_ for _ in ()).throw(RuntimeError("backend unreachable")),
    )
    sent = []

    class FakeTransport:
        def send(self, message):
            sent.append(message)

    monkeypatch.setattr("ops.notify.push.build_push_transport", lambda cfg: FakeTransport())
    with pytest.raises(RuntimeError, match="backend unreachable"):
        CliRunner().invoke(
            cli_mod.cli, ["research", "run", "--notify"], catch_exceptions=False,
        )
    assert len(sent) == 1
    assert sent[0].title == "research run FAILED"
    assert sent[0].urgency == "high"


def test_notify_not_sent_without_flag(env, monkeypatch):
    _seed_hits(env, ["AAA"])
    monkeypatch.setattr(
        "ops.research.brain.research_hit",
        lambda hit, **kw: ResearchOutcome(
            symbol=hit["symbol"], hit_id=hit["id"], status="researched",
            memo_id="m-AAA", recommendation="buy",
        ),
    )
    sent = []
    monkeypatch.setattr(
        "ops.notify.push.build_push_transport",
        lambda cfg: type("T", (), {"send": lambda self, m: sent.append(m)})(),
    )
    result = CliRunner().invoke(cli_mod.cli, ["research", "run"])
    assert result.exit_code == 0, result.output
    assert sent == []
