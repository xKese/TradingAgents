from unittest import mock

import pytest
from typer.testing import CliRunner

import cli.main as m


@pytest.mark.unit
def test_analyze_reports_missing_windows_console_without_traceback():
    runner = CliRunner()

    with mock.patch.object(m, "run_analysis", side_effect=m.NoConsoleScreenBufferError):
        result = runner.invoke(m.app, [])

    assert result.exit_code != 0
    assert "No Windows console found" in result.output
    assert "Run TradingAgents from an interactive terminal" in result.output
    assert "Traceback" not in result.output


@pytest.mark.unit
def test_analyze_preserves_other_exceptions():
    runner = CliRunner()

    with mock.patch.object(m, "run_analysis", side_effect=RuntimeError("boom")):
        result = runner.invoke(m.app, [], catch_exceptions=True)

    assert isinstance(result.exception, RuntimeError)
    assert str(result.exception) == "boom"
