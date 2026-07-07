"""ChatGPT subscription-backed OpenAI models via LangChain's Codex client."""

from __future__ import annotations

import os
from typing import Any

from .base_client import BaseLLMClient, normalize_content
from .validators import validate_model

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
                return normalize_content(super().invoke(input, config, **kwargs))

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
