import importlib

import pytest

import tradingagents.default_config as default_config
from cli.main import _build_run_config

SELECTIONS = {
    "research_depth": 1,
    "shallow_thinker": "quick",
    "deep_thinker": "deep",
    "backend_url": None,
    "llm_provider": "openai",
    "output_language": "English",
}


def test_extension_defaults_preserve_noop_behavior():
    config = default_config.DEFAULT_CONFIG
    assert config["citation_validation_enabled"] is True
    assert config["strict_temporal_grounding"] is True
    assert config["offline_mode"] is False
    assert config["operational_fixture_path"] is None
    assert config["operational_max_filings"] == 4
    assert config["sec_user_agent"] is None
    assert config["local_tracing_enabled"] is False
    assert config["external_tracing_enabled"] is False
    assert config["external_tracing_provider"] == "langsmith"
    assert config["external_tracing_project"] is None
    assert config["trace_capture_content"] is False
    assert config["trace_max_bytes"] == 10_000_000
    assert config["observability_pricing"] == {}


def test_extension_cli_flags_override_defaults(tmp_path):
    config = _build_run_config(
        SELECTIONS,
        None,
        citation_validation=False,
        strict_temporal=False,
        local_tracing=True,
        external_tracing=False,
        trace_output=tmp_path / "trace.jsonl",
        offline=True,
    )
    assert config["citation_validation_enabled"] is False
    assert config["strict_temporal_grounding"] is False
    assert config["local_tracing_enabled"] is True
    assert config["trace_output_path"] == str(tmp_path / "trace.jsonl")
    assert config["offline_mode"] is True


def test_extension_environment_precedence(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_LOCAL_TRACING_ENABLED", "true")
    monkeypatch.setenv("TRADINGAGENTS_STRICT_TEMPORAL_GROUNDING", "false")
    monkeypatch.setenv("TRADINGAGENTS_TRACE_OUTPUT_PATH", "/tmp/extension-trace.jsonl")
    monkeypatch.setenv("TRADINGAGENTS_OPERATIONAL_MAX_FILINGS", "7")
    monkeypatch.setenv("TRADINGAGENTS_TRACE_MAX_BYTES", "2048")
    reloaded = importlib.reload(default_config)
    assert reloaded.DEFAULT_CONFIG["local_tracing_enabled"] is True
    assert reloaded.DEFAULT_CONFIG["strict_temporal_grounding"] is False
    assert reloaded.DEFAULT_CONFIG["trace_output_path"] == "/tmp/extension-trace.jsonl"
    assert reloaded.DEFAULT_CONFIG["operational_max_filings"] == 7
    assert reloaded.DEFAULT_CONFIG["trace_max_bytes"] == 2048
    monkeypatch.delenv("TRADINGAGENTS_LOCAL_TRACING_ENABLED")
    monkeypatch.delenv("TRADINGAGENTS_STRICT_TEMPORAL_GROUNDING")
    monkeypatch.delenv("TRADINGAGENTS_TRACE_OUTPUT_PATH")
    monkeypatch.delenv("TRADINGAGENTS_OPERATIONAL_MAX_FILINGS")
    monkeypatch.delenv("TRADINGAGENTS_TRACE_MAX_BYTES")
    importlib.reload(default_config)


def test_extension_boolean_environment_values_are_typed(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_LOCAL_TRACING_ENABLED", "not-a-boolean")
    with pytest.raises(ValueError, match="TRADINGAGENTS_LOCAL_TRACING_ENABLED"):
        importlib.reload(default_config)
    monkeypatch.delenv("TRADINGAGENTS_LOCAL_TRACING_ENABLED")
    importlib.reload(default_config)
