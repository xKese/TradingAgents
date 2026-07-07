"""ChatGPT subscription-backed OpenAI models via LangChain's Codex client."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

logger = logging.getLogger(__name__)


def _stream_retryable_exceptions() -> tuple[type[BaseException], ...]:
    """Transport failures that can hit mid-stream, after the SDK's own
    ``max_retries`` no longer applies (the request succeeded; the SSE stream
    died halfway). Chat completions are stateless, so re-invoking is safe.
    """
    import httpx

    exceptions: list[type[BaseException]] = [httpx.TransportError]
    try:
        import openai

        exceptions += [openai.APIConnectionError, openai.APITimeoutError]
    except ImportError:
        pass
    return tuple(exceptions)

_PASSTHROUGH_KWARGS = (
    "timeout",
    "max_retries",
    "reasoning_effort",
    "temperature",
    "callbacks",
)


def _missing_chatgpt_token_error() -> FileNotFoundError:
    return FileNotFoundError(
        "No ChatGPT OAuth token found for provider 'openai_codex'. Run "
        "`python -c \"from langchain_openai.chatgpt_oauth import "
        "login_chatgpt; login_chatgpt()\"` once, or use "
        "`login_chatgpt_device()` on a headless machine. Tokens are "
        "stored by LangChain at ~/.langchain/chatgpt-auth.json."
    )


class OpenAICodexClient(BaseLLMClient):
    """Client for ChatGPT OAuth-backed Codex/OpenAI models.

    This provider intentionally uses LangChain's experimental
    ``_ChatOpenAICodex`` rather than reimplementing the ChatGPT OAuth and Codex
    backend wire protocol here. The class is private/upstream-experimental, so
    imports stay lazy and error messages are explicit.
    """

    provider = "openai_codex"

    def get_llm(self) -> Any:
        self.warn_if_unknown_model()
        if self.base_url:
            raise ValueError(
                "Provider 'openai_codex' uses the fixed ChatGPT Codex backend and "
                "does not accept backend_url / TRADINGAGENTS_LLM_BACKEND_URL."
            )

        try:
            from langchain_openai.chat_models.codex import _ChatOpenAICodex
        except (ImportError, ModuleNotFoundError) as exc:
            raise ImportError(
                "Provider 'openai_codex' requires LangChain's experimental "
                "ChatGPT OAuth Codex client. Upgrade with: "
                "pip install -U 'langchain-openai>=1.3.3' "
                "'langchain-core>=1.4.7' 'openai>=2.26.0'."
            ) from exc

        class NormalizedChatOpenAICodex(_ChatOpenAICodex):
            def _get_request_payload(self, input_, *, stop=None, **kwargs):
                try:
                    return super()._get_request_payload(input_, stop=stop, **kwargs)
                except FileNotFoundError as exc:
                    raise _missing_chatgpt_token_error() from exc

            def invoke(self, input, config=None, **kwargs):
                # The SDK's max_retries only covers request setup; a stream
                # that breaks mid-body raises through invoke and would kill a
                # long multi-agent run. Retry the whole (stateless) call with
                # the same budget.
                retryable = _stream_retryable_exceptions()
                attempts = max(1, int(getattr(self, "max_retries", None) or 2) + 1)
                for attempt in range(attempts):
                    try:
                        return normalize_content(super().invoke(input, config, **kwargs))
                    except retryable as exc:
                        if attempt == attempts - 1:
                            raise
                        delay = min(2 ** attempt, 30)
                        logger.warning(
                            "openai_codex stream failed mid-call (%s); retry %d/%d in %ds",
                            exc,
                            attempt + 1,
                            attempts - 1,
                            delay,
                        )
                        time.sleep(delay)

        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "originator": os.environ.get("TRADINGAGENTS_CODEX_ORIGINATOR")
            or "tradingagents",
        }

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        try:
            return NormalizedChatOpenAICodex(**llm_kwargs)
        except FileNotFoundError as exc:
            raise _missing_chatgpt_token_error() from exc

    def validate_model(self) -> bool:
        return validate_model(self.provider, self.model)
