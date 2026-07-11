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
from collections.abc import Iterable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from .base_client import BaseLLMClient
from .validators import validate_model

logger = logging.getLogger(__name__)

CODEX_INSTALL_COMMAND = 'python -m pip install -e ".[codex]"'
CODEX_UPDATE_COMMAND = "python -m pip install -U --pre openai-codex"
CODEX_LOGIN_COMMAND = "codex login"
CODEX_CLI_UPDATE_COMMAND = "codex update"
CODEX_ACCOUNT_CHECK_COMMAND = (
    'python -c "from openai_codex import Codex; '
    'c=Codex(); print(c.account()); c.close()"'
)
CODEX_MODEL_LIST_COMMAND = (
    'python -c "from openai_codex import Codex; '
    'c=Codex(); print([m.id for m in c.models().data]); c.close()"'
)


class CodexSetupError(RuntimeError):
    """Actionable setup error for the experimental Codex provider."""


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


def _available_model_ids(codex: Any) -> list[str]:
    """Return model IDs advertised by the installed Codex SDK/runtime."""
    try:
        models = codex.models()
    except Exception as exc:  # noqa: BLE001 - availability check is best-effort
        logger.warning("Could not list Codex models before invocation: %s", exc)
        return []

    data = getattr(models, "data", models)
    ids = []
    for model in data or []:
        model_id = getattr(model, "id", None) or getattr(model, "model", None)
        if model_id:
            ids.append(str(model_id))
    return ids


def _codex_setup_message(
    problem: str,
    *,
    original_error: str | None = None,
    available_models: Iterable[str] | None = None,
) -> str:
    lines = [
        problem,
        "",
        "To fix Codex setup:",
        f"1. Install or update the Codex Python SDK: {CODEX_UPDATE_COMMAND}",
        f"2. If the Codex CLI is available, update it too: {CODEX_CLI_UPDATE_COMMAND}",
        f"3. If you are not signed in, run: {CODEX_LOGIN_COMMAND}",
        f"4. If `codex` is not recognized, verify local SDK auth with: {CODEX_ACCOUNT_CHECK_COMMAND}",
        f"5. List models your installed runtime can run with: {CODEX_MODEL_LIST_COMMAND}",
    ]
    if available_models:
        lines.extend(["", "Models currently reported by your installed runtime:"])
        lines.append(", ".join(available_models))
    if original_error:
        lines.extend(["", f"Original Codex error: {original_error}"])
    return "\n".join(lines)


def _looks_like_newer_codex_required(message: str) -> bool:
    lowered = message.lower()
    return "requires a newer version of codex" in lowered or "update codex" in lowered


def _looks_like_auth_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "not authenticated",
            "authentication",
            "unauthorized",
            "login",
            "sign in",
            "requires_openai_auth",
        )
    )


def preflight_codex_runtime(models: str | Iterable[str]) -> list[str]:
    """Validate Codex SDK/auth/model availability before the graph starts."""
    try:
        from openai_codex import Codex
    except ImportError as exc:
        raise CodexSetupError(
            _codex_setup_message(
                "Codex provider is selected, but the `openai-codex` SDK is not installed.",
                original_error=str(exc),
            )
        ) from exc

    requested_models = [models] if isinstance(models, str) else list(models)
    try:
        with Codex() as codex:
            try:
                account = codex.account()
            except Exception as exc:  # noqa: BLE001 - convert SDK detail to user steps
                raise CodexSetupError(
                    _codex_setup_message(
                        "Codex provider is selected, but TradingAgents could not verify "
                        "a local Codex/ChatGPT login.",
                        original_error=str(exc),
                    )
                ) from exc

            if not account:
                raise CodexSetupError(
                    _codex_setup_message(
                        "Codex provider is selected, but no local Codex/ChatGPT "
                        "account was found."
                    )
                )

            available = _available_model_ids(codex)
    except CodexSetupError:
        raise
    except Exception as exc:  # noqa: BLE001 - SDK startup/runtime failure
        message = str(exc)
        if _looks_like_auth_error(message):
            problem = (
                "Codex provider is selected, but the local Codex/ChatGPT "
                "session is not ready."
            )
        else:
            problem = "Codex provider is selected, but the Codex SDK/runtime is not ready."
        raise CodexSetupError(_codex_setup_message(problem, original_error=message)) from exc

    missing = [model for model in requested_models if available and model not in available]
    if missing:
        problem = (
            "Codex provider is selected, but the configured model is not available "
            "in your installed Codex SDK/runtime."
        )
        if any(model.startswith("gpt-5.6") for model in missing):
            problem += " GPT-5.6 models may require a newer Codex app/CLI/SDK."
        raise CodexSetupError(
            _codex_setup_message(
                problem,
                available_models=available,
                original_error=f"Unavailable model(s): {', '.join(missing)}",
            )
        )
    return available


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
                available = _available_model_ids(codex)
                if available and self.model_name not in available:
                    raise CodexSetupError(
                        _codex_setup_message(
                            "The configured Codex model is not available in your "
                            "installed Codex SDK/runtime.",
                            available_models=available,
                            original_error=f"Unavailable model: {self.model_name}",
                        )
                    )
                thread = codex.thread_start(
                    model=self.model_name,
                    sandbox=Sandbox.read_only,
                )
                result = thread.run(prompt)
        except CodexSetupError:
            raise
        except Exception as exc:
            message = str(exc)
            if _looks_like_newer_codex_required(message):
                problem = "This Codex model requires a newer Codex app/CLI/SDK."
            elif _looks_like_auth_error(message):
                problem = (
                    "TradingAgents could not use your local Codex/ChatGPT login "
                    "for this request."
                )
            else:
                problem = "Codex local invocation failed."
            raise RuntimeError(
                _codex_setup_message(problem, original_error=message)
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
        preflight_codex_runtime(self.model)
        return CodexChatModel(model_name=self.model)

    def validate_model(self) -> bool:
        return validate_model("codex", self.model)
