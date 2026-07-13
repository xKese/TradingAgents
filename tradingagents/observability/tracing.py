"""Dependency-light local tracing and optional LangSmith configuration."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

_SENSITIVE_KEY = re.compile(
    r"api.?key|authorization|bearer|secret|password|auth.?token|access.?token|"
    r"refresh.?token|cookie|credential|environment",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+|\bsk-[A-Za-z0-9_-]{8,}\b"
)


def redact(value: Any) -> Any:
    """Recursively redact secrets while retaining useful trace structure."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SENSITIVE_VALUE.sub(lambda match: f"{match.group(1) or ''}[REDACTED]", value)
    return value


def configuration_hash(config: dict[str, Any]) -> str:
    """Hash a redacted, JSON-safe configuration for run comparison."""
    safe = redact(config)
    payload = json.dumps(safe, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class NoOpTracer:
    """Disabled tracing implementation with no side effects."""

    callback: BaseCallbackHandler | None = None

    def start_run(self, **_: Any) -> str | None:
        return None

    def record(self, _event_type: str, **_: Any) -> None:
        return None

    def end_run(self, **_: Any) -> None:
        return None


class LocalJSONLTracer:
    """Append-only, rotated JSONL tracer safe for use from callback threads."""

    def __init__(
        self,
        path: str | Path,
        *,
        config: dict[str, Any],
        max_bytes: int = 10_000_000,
        capture_content: bool = False,
    ) -> None:
        self.path = Path(path).expanduser()
        self.max_bytes = max(1, int(max_bytes))
        self.capture_content = capture_content
        self.config_hash = configuration_hash(config)
        self._lock = threading.Lock()
        self.run_id: str | None = None
        self.context: dict[str, Any] = {}
        self.callback = TraceCallbackHandler(self, config=config)

    def _rotate_if_needed(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        rotated = self.path.with_suffix(self.path.suffix + ".1")
        if rotated.exists():
            rotated.unlink()
        self.path.replace(rotated)

    def start_run(
        self,
        *,
        ticker: str,
        analysis_date: str,
        checkpoint_resumed: bool = False,
        **metadata: Any,
    ) -> str:
        self.run_id = str(uuid.uuid4())
        self.context = {
            "run_id": self.run_id,
            "ticker": ticker,
            "analysis_date": analysis_date,
            "configuration_hash": self.config_hash,
        }
        self.record(
            "graph_start",
            checkpoint_resume_status="resumed" if checkpoint_resumed else "fresh",
            **metadata,
        )
        return self.run_id

    def record(self, event_type: str, **fields: Any) -> None:
        event = {
            **self.context,
            "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        event = redact(event)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._rotate_if_needed()
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        except OSError as exc:
            logger.warning("Local trace write failed for %s: %s", self.path, exc)

    def end_run(self, *, status: str, **fields: Any) -> None:
        self.record("graph_end", final_graph_status=status, **fields)


class TraceCallbackHandler(BaseCallbackHandler):
    """LangChain callback bridge for LLM, tool, and node trace events."""

    def __init__(self, tracer: LocalJSONLTracer, *, config: dict[str, Any]) -> None:
        super().__init__()
        self.tracer = tracer
        self.provider = str(config.get("llm_provider", ""))
        self.default_model = str(config.get("quick_think_llm", ""))
        self.pricing = config.get("observability_pricing", {}) or {}
        self._started: dict[str, float] = {}
        self._retry_counts: dict[str, int] = {}

    @staticmethod
    def _id(value: Any) -> str | None:
        return str(value) if value is not None else None

    def _start(self, run_id: Any) -> None:
        if run_id is not None:
            self._started[str(run_id)] = time.monotonic()

    def _duration_ms(self, run_id: Any) -> float | None:
        started = self._started.pop(str(run_id), None) if run_id is not None else None
        if started is None:
            return None
        return round((time.monotonic() - started) * 1000, 3)

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        self._start(run_id)
        prompt_hash = hashlib.sha256("\n".join(prompts).encode("utf-8")).hexdigest()[:16]
        fields: dict[str, Any] = {
            "span_id": self._id(run_id),
            "parent_run_id": self._id(parent_run_id),
            "llm_provider": self.provider,
            "model_identifier": kwargs.get("invocation_params", {}).get(
                "model_name",
                self.default_model,
            ),
            "prompt_template_hash": prompt_hash,
        }
        if self.tracer.capture_content:
            fields["prompt_preview"] = [prompt[:500] for prompt in prompts]
        self.tracer.record("llm_start", **fields)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **kwargs: Any,
    ) -> None:
        prompt_repr = repr(messages)
        self.on_llm_start(
            serialized,
            [prompt_repr],
            run_id=run_id,
            parent_run_id=parent_run_id,
            **kwargs,
        )

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        usage = None
        try:
            message = response.generations[0][0].message
            if isinstance(message, AIMessage):
                usage = message.usage_metadata
        except (AttributeError, IndexError, TypeError):
            usage = None
        tokens_in = usage.get("input_tokens") if usage else None
        tokens_out = usage.get("output_tokens") if usage else None
        model = self.default_model
        estimated_cost = self._estimate_cost(model, tokens_in, tokens_out)
        self.tracer.record(
            "llm_end",
            span_id=self._id(run_id),
            parent_run_id=self._id(parent_run_id),
            duration_ms=self._duration_ms(run_id),
            token_usage={"input": tokens_in, "output": tokens_out},
            token_metadata_available=usage is not None,
            estimated_cost=estimated_cost,
            estimated_cost_configured=estimated_cost is not None,
            retry_count=self._retry_counts.pop(str(run_id), 0),
        )

    def _estimate_cost(
        self,
        model: str,
        tokens_in: int | None,
        tokens_out: int | None,
    ) -> float | None:
        rates = self.pricing.get(model)
        if not rates or tokens_in is None or tokens_out is None:
            return None
        if "input_per_million" not in rates or "output_per_million" not in rates:
            return None
        return round(
            (tokens_in / 1_000_000) * float(rates["input_per_million"])
            + (tokens_out / 1_000_000) * float(rates["output_per_million"]),
            8,
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        self.tracer.record(
            "llm_error",
            span_id=self._id(run_id),
            parent_run_id=self._id(parent_run_id),
            duration_ms=self._duration_ms(run_id),
            exception_type=type(error).__name__,
            error_message=str(error)[:500],
        )

    def on_retry(self, retry_state: Any, *, run_id: Any = None, **_: Any) -> None:
        key = str(run_id)
        self._retry_counts[key] = self._retry_counts.get(key, 0) + 1

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        self._start(run_id)
        serialized = serialized or {}
        fields: dict[str, Any] = {
            "span_id": self._id(run_id),
            "parent_run_id": self._id(parent_run_id),
            "tool_name": serialized.get("name") or serialized.get("id"),
            "tool_input_hash": hashlib.sha256(input_str.encode("utf-8")).hexdigest()[:16],
        }
        if self.tracer.capture_content:
            fields["tool_input_preview"] = input_str[:500]
        self.tracer.record("tool_start", **fields)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        fields: dict[str, Any] = {
            "span_id": self._id(run_id),
            "parent_run_id": self._id(parent_run_id),
            "duration_ms": self._duration_ms(run_id),
            "tool_output_available": output is not None,
        }
        if self.tracer.capture_content:
            fields["tool_output_preview"] = str(output)[:500]
        self.tracer.record("tool_end", **fields)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        self.tracer.record(
            "tool_error",
            span_id=self._id(run_id),
            parent_run_id=self._id(parent_run_id),
            duration_ms=self._duration_ms(run_id),
            exception_type=type(error).__name__,
            error_message=str(error)[:500],
        )

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        name: str | None = None,
        **_: Any,
    ) -> None:
        self._start(run_id)
        serialized = serialized or {}
        self.tracer.record(
            "node_start",
            span_id=self._id(run_id),
            parent_run_id=self._id(parent_run_id),
            graph_node=name or serialized.get("name") or serialized.get("id"),
        )

    def on_chain_end(
        self,
        outputs: dict[str, Any],
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        self.tracer.record(
            "node_end",
            span_id=self._id(run_id),
            parent_run_id=self._id(parent_run_id),
            duration_ms=self._duration_ms(run_id),
        )

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: Any = None,
        parent_run_id: Any = None,
        **_: Any,
    ) -> None:
        self.tracer.record(
            "node_error",
            span_id=self._id(run_id),
            parent_run_id=self._id(parent_run_id),
            duration_ms=self._duration_ms(run_id),
            exception_type=type(error).__name__,
            error_message=str(error)[:500],
        )


def configure_external_tracing(config: dict[str, Any]) -> None:
    """Enable LangSmith through documented LangChain environment conventions."""
    if not config.get("external_tracing_enabled"):
        return
    provider = str(config.get("external_tracing_provider", "langsmith")).lower()
    if provider != "langsmith":
        raise ValueError(f"Unsupported external tracing provider: {provider}")
    try:
        import langsmith  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "External LangSmith tracing requires the observability extra: "
            "pip install -e '.[observability]'"
        ) from exc
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    project = config.get("external_tracing_project")
    if project:
        os.environ.setdefault("LANGSMITH_PROJECT", str(project))


def create_tracer(config: dict[str, Any]) -> NoOpTracer | LocalJSONLTracer:
    """Build disabled/local tracing and independently configure external mode."""
    configure_external_tracing(config)
    if not config.get("local_tracing_enabled"):
        return NoOpTracer()
    return LocalJSONLTracer(
        config["trace_output_path"],
        config=config,
        max_bytes=int(config.get("trace_max_bytes", 10_000_000)),
        capture_content=bool(config.get("trace_capture_content", False)),
    )
