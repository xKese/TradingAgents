"""Unit tests for `ops research monitor` (monitor core faked)."""

import pytest
from click.testing import CliRunner

import ops.cli as cli_mod
from ops.research.monitor import MonitorOutcome

pytestmark = pytest.mark.unit


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_JOURNAL_PATH", str(tmp_path / "journal.sqlite"))
    monkeypatch.setenv("OPS_SCREEN_STORE_PATH", str(tmp_path / "screen.sqlite"))
    monkeypatch.setenv("OPS_MEMO_STORE_PATH", str(tmp_path / "memos.sqlite"))
    return tmp_path


def test_monitor_echoes_summary(env, monkeypatch):
    outcome = MonitorOutcome(
        asof="2026-07-07", memos_checked=3, falsifiers_evaluated=5,
        tripped=1, unevaluable=1, escalations=1, resolution_due=1,
        catalyst_due=0, errors=["WIDG: yahoo exploded"],
    )
    monkeypatch.setattr("ops.research.monitor.monitor_memos", lambda **kw: outcome)
    result = CliRunner().invoke(cli_mod.cli, ["research", "monitor"])
    assert result.exit_code == 0, result.output
    assert "3 memos" in result.output
    assert "1 tripped" in result.output
    assert "yahoo exploded" in result.output


def test_monitor_empty_stores_clean_exit(env):
    # Real stores, real (empty) journal, no fakes: must be a quiet no-op.
    result = CliRunner().invoke(cli_mod.cli, ["research", "monitor"])
    assert result.exit_code == 0, result.output
