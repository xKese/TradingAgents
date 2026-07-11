"""Experimental Codex local client.

This provider lets users who have Codex through a ChatGPT plan run the
TradingAgents workflow through their local Codex authentication instead of an
OpenAI Platform API key. It intentionally uses the published ``openai-codex``
SDK rather than reading Codex credential files directly.

Codex is an agent surface, not a Chat Completions-compatible model API. The
adapter below provides the small LangChain chat-model surface this project
needs, including a JSON-mediated bridge for tool calls.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from .base_client import BaseLLMClient
from .validators import validate_model

logger = logging.getLogger(__name__)


def _message_to_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "\n".join(part for part in parts if part)
    return str(content)


def _messages_to_prompt(messages: list[BaseMessage]) -> str:
    chunks = []
    for message in messages:
        role = getattr(message, "type", message.__class__.__name__)
        text = _message_to_text(message).strip()
        if text:
            chunks.append(f"{role.upper()}:\n{text}")
    return "\n\n".join(chunks)


def _tool_schema(tool: Any) -> dict[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        parameters = args_schema.model_json_schema()
    else:
        parameters = getattr(tool, "args", {}) or {}
    return {
        "name": getattr(tool, "name", tool.__class__.__name__),
        "description": getattr(tool, "description", "") or "",
        "parameters": parameters,
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


class CodexChatModel(BaseChatModel):
    """LangChain chat wrapper around the local Codex SDK."""

    model_name: str = "gpt-5.4"
    tools: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "codex-local"

    def bind_tools(self, tools, **kwargs):  # noqa: D401 - LangChain signature
        """Return a copy of this model configured with LangChain tools."""
        return self.model_copy(update={"tools": list(tools)})

    def with_structured_output(self, schema, *, method=None, **kwargs):
        raise NotImplementedError(
            "Codex local provider does not expose native structured output; "
            "TradingAgents will fall back to free-text generation."
        )

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        prompt = _messages_to_prompt(messages)
        if self.tools:
            prompt = self._add_tool_protocol(prompt)

        raw = self._run_codex(prompt)
        message = self._to_ai_message(raw)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _add_tool_protocol(self, prompt: str) -> str:
        tool_specs = [_tool_schema(tool) for tool in self.tools]
        return (
            f"{prompt}\n\n"
            "You may either answer directly or request tool calls. If you need tools, "
            "return ONLY valid JSON in this exact shape:\n"
            '{"tool_calls":[{"name":"tool_name","args":{}}]}\n'
            "If you have enough information for a final answer, return normal prose "
            "and do not wrap it in JSON.\n\n"
            f"Available tools:\n{json.dumps(tool_specs, indent=2)}"
        )

    def _run_codex(self, prompt: str) -> str:
        try:
            from openai_codex import Codex, Sandbox
        except ImportError as exc:
            raise ImportError(
                "The Codex provider requires the optional SDK. Install it with "
                '`pip install ".[codex]"` or `pip install openai-codex`, then run '
                "`codex login` and sign in with ChatGPT."
            ) from exc

        try:
            with Codex() as codex:
                thread = codex.thread_start(
                    model=self.model_name,
                    sandbox=Sandbox.read_only,
                )
                result = thread.run(prompt)
        except Exception as exc:
            raise RuntimeError(
                "Codex local invocation failed. Make sure the Codex CLI/SDK is "
                "installed and authenticated with `codex login`."
            ) from exc

        return str(getattr(result, "final_response", "") or "")

    def _to_ai_message(self, raw: str) -> AIMessage:
        parsed = _extract_json_object(raw) if self.tools else None
        tool_calls = parsed.get("tool_calls") if isinstance(parsed, dict) else None
        if isinstance(tool_calls, list) and tool_calls:
            normalized = []
            for idx, call in enumerate(tool_calls):
                if not isinstance(call, dict) or not call.get("name"):
                    continue
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    logger.warning("Ignoring Codex tool call with non-object args: %s", call)
                    continue
                normalized.append({
                    "name": str(call["name"]),
                    "args": args,
                    "id": str(call.get("id") or f"codex_tool_{idx}"),
                })
            if normalized:
                return AIMessage(content="", tool_calls=normalized)
        return AIMessage(content=raw)


class CodexClient(BaseLLMClient):
    """Client for Codex local/ChatGPT-authenticated runs."""

    def get_llm(self) -> Any:
        self.warn_if_unknown_model()
        return CodexChatModel(model_name=self.model)

    def validate_model(self) -> bool:
        return validate_model("codex", self.model)
