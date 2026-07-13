import builtins
import json
from types import SimpleNamespace

import pytest

from tradingagents.observability import (
    LocalJSONLTracer,
    NoOpTracer,
    collect_evidence_ids,
    create_tracer,
)


def _config(tmp_path, **overrides):
    config = {
        "local_tracing_enabled": True,
        "external_tracing_enabled": False,
        "trace_output_path": str(tmp_path / "trace.jsonl"),
        "trace_max_bytes": 1_000_000,
        "trace_capture_content": False,
        "llm_provider": "test-provider",
        "quick_think_llm": "test-model",
        "observability_pricing": {},
        "api_key": "should-not-be-logged",
    }
    config.update(overrides)
    return config


def test_disabled_tracer_is_noop(tmp_path):
    tracer = create_tracer(
        _config(tmp_path, local_tracing_enabled=False, external_tracing_enabled=False)
    )
    assert isinstance(tracer, NoOpTracer)
    assert tracer.start_run(ticker="TEST", analysis_date="2024-01-01") is None
    tracer.record("anything", api_key="secret")
    assert not (tmp_path / "trace.jsonl").exists()


def test_evidence_id_collection_handles_none_and_malformed_entries():
    assert collect_evidence_ids(None) == []
    assert collect_evidence_ids("not-a-list") == []
    assert collect_evidence_ids(
        [None, "bad", {"other": "value"}, {"evidence_id": "EVID-VALID"}]
    ) == ["EVID-VALID"]


def test_local_tracer_emits_json_and_redacts_secrets(tmp_path):
    tracer = LocalJSONLTracer(tmp_path / "trace.jsonl", config=_config(tmp_path))
    tracer.start_run(ticker="TEST", analysis_date="2024-01-01")
    tracer.record(
        "custom",
        api_key="secret",
        authorization="Bearer abcdefghijklmnop",
        nested={"password": "hidden"},
    )
    tracer.end_run(status="completed")
    records = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert [record["event_type"] for record in records] == [
        "graph_start",
        "custom",
        "graph_end",
    ]
    assert records[1]["api_key"] == "[REDACTED]"
    assert records[1]["authorization"] == "[REDACTED]"
    assert records[1]["nested"]["password"] == "[REDACTED]"


def test_node_failure_and_missing_token_metadata_are_recorded(tmp_path):
    tracer = LocalJSONLTracer(tmp_path / "trace.jsonl", config=_config(tmp_path))
    tracer.start_run(ticker="TEST", analysis_date="2024-01-01")
    callback = tracer.callback
    callback.on_chain_start({}, {}, run_id="node-1", name="Operational Signals Analyst")
    callback.on_chain_error(RuntimeError("boom"), run_id="node-1")
    callback.on_llm_start({}, ["prompt"], run_id="llm-1")
    callback.on_llm_end(SimpleNamespace(generations=[]), run_id="llm-1")
    records = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    error = next(record for record in records if record["event_type"] == "node_error")
    llm_end = next(record for record in records if record["event_type"] == "llm_end")
    assert error["exception_type"] == "RuntimeError"
    assert llm_end["token_metadata_available"] is False
    assert llm_end["estimated_cost"] is None


def test_external_dependency_is_optional(monkeypatch, tmp_path):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "langsmith":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(RuntimeError, match="observability"):
        create_tracer(
            _config(
                tmp_path,
                local_tracing_enabled=False,
                external_tracing_enabled=True,
            )
        )
