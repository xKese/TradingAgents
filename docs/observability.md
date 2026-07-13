# Observability

The fork adds three tracing modes without changing execution when tracing is
disabled.

## Disabled/no-op

This is the default:

```bash
TRADINGAGENTS_LOCAL_TRACING_ENABLED=false
TRADINGAGENTS_EXTERNAL_TRACING_ENABLED=false
```

`NoOpTracer` has no file, network, or callback side effects.

## Local structured tracing

Enable append-only JSONL tracing:

```bash
TRADINGAGENTS_LOCAL_TRACING_ENABLED=true
TRADINGAGENTS_TRACE_OUTPUT_PATH=~/.tradingagents/logs/traces.jsonl
```

Equivalent CLI flags are `--local-tracing` and `--trace-output PATH`.

`LocalJSONLTracer` records graph, node, LLM, and tool events when LangChain
metadata is available. Fields include run/parent/span IDs, ticker, analysis
date, timestamps, duration, node/tool identity, provider/model, retry count,
token metadata availability, token counts, explicit-price-only estimated cost,
prompt hash, configuration hash, errors, evidence IDs, citation validation,
checkpoint resume status, and final graph status.

Provider callbacks do not always expose every field. Missing usage metadata is
recorded as unavailable; cost is `null` unless the caller explicitly supplies
`observability_pricing` for the model. The tracer never claims that an estimate
is accurate when token metadata or configured rates are absent.

### Rotation and cleanup

The default maximum is 10 MB (`trace_max_bytes`). Before the next append, the
file rotates to `<trace>.1`; only one rotated file is retained. Operators should
archive or delete traces according to their own retention policy. Disable local
tracing to stop writes. `TRADINGAGENTS_TRACE_MAX_BYTES` overrides the limit.

## External tracing (LangSmith)

Install the optional extra:

```bash
pip install -e ".[observability]"
```

Then use LangSmith's documented environment variables, for example:

```bash
export LANGSMITH_API_KEY="..."
export LANGSMITH_PROJECT="tradingagents-research"
export TRADINGAGENTS_EXTERNAL_TRACING_ENABLED=true
```

The extension sets `LANGSMITH_TRACING=true` only after external tracing is
explicitly enabled. It relies on LangGraph/LangChain's established integration
rather than introducing a separate export client. If the optional dependency
is absent, enabling external mode raises a clear installation error; base
installs and local tracing remain unaffected.

`external_tracing_provider` defaults to `langsmith`; its environment override
is `TRADINGAGENTS_EXTERNAL_TRACING_PROVIDER`. An optional
`TRADINGAGENTS_EXTERNAL_TRACING_PROJECT` value is copied to `LANGSMITH_PROJECT`
only when LangSmith has not already set that variable.

## Privacy and redaction

By default, full prompts, tool inputs, tool outputs, and source documents are
not stored. The tracer stores hashes and structural metadata. Keys containing
API-key, authorization, secret, password, authentication-token, cookie,
credential, or environment terms are redacted recursively, as are bearer
tokens and common `sk-...` token strings. Token-usage counters remain visible
because they do not contain credentials.

Verbose previews require explicit opt-in:

```bash
TRADINGAGENTS_TRACE_CAPTURE_CONTENT=true
```

Even then, previews are length-bounded and passed through redaction. Operators
should still treat trace files as potentially sensitive research artifacts and
must not commit them.

## JSONL schema

Every line is one JSON object with a shared envelope:

```json
{
  "run_id": "uuid",
  "event_type": "tool_end",
  "timestamp": "ISO-8601 UTC",
  "ticker": "SBLG",
  "analysis_date": "2024-03-31",
  "configuration_hash": "hex",
  "span_id": "provider run id",
  "parent_run_id": "provider parent id",
  "duration_ms": 12.3
}
```

Event-specific fields are additive. Schema evolution is backward-compatible:
consumers should tolerate unknown fields and absent provider-dependent fields.
