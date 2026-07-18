"""JSON-serialisable views of the CLI selection catalogs for the web UI.

Everything here is a thin adapter over the existing single-source-of-truth
catalogs used by the interactive CLI, so the browser form always offers the
same providers, models, depths, and analysts as ``tradingagents`` on the
terminal — without importing any ``questionary`` prompt machinery.
"""

from __future__ import annotations

import os

from cli.models import AnalystType, AssetType
from cli.utils import (
    ANALYST_ORDER,
    _fetch_openrouter_models,
    _llm_provider_table,
    detect_asset_type,
)
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.model_catalog import MODEL_OPTIONS, get_model_options
from tradingagents.runtime import running_in_docker

# Research-depth options mirror cli.utils.select_research_depth's DEPTH_OPTIONS.
# Kept here as data (the CLI defines them inline inside a questionary prompt).
DEPTH_OPTIONS: list[tuple[str, int]] = [
    ("Shallow — schnelle Analyse, wenige Debattenrunden", 1),
    ("Medium — ausgewogen, moderate Debattenrunden", 3),
    ("Deep — umfassend, tiefe Debatten- und Strategierunden", 5),
]

# Providers that authenticate without a single API key env var but still need a
# reachable endpoint the user typically points at their own host.
_LOCAL_PROVIDERS = {"ollama", "openai_compatible"}


def _model_list(options: list[tuple[str, str]]) -> list[dict]:
    """Turn ``[(display, model_id), ...]`` into ``[{id, label, custom}]``.

    The catalog uses the sentinel id ``"custom"`` to mean "let the user type a
    model id"; surface that as a ``custom`` flag so the UI can render a free-text
    field instead of a normal option.
    """
    out = []
    for display, model_id in options:
        out.append(
            {"id": model_id, "label": display, "custom": model_id == "custom"}
        )
    return out


def providers() -> list[dict]:
    """Return every supported provider with endpoint and local key status."""
    result = []
    for display, key, base_url in _llm_provider_table():
        key_env = get_api_key_env(key)
        key_present = bool(key_env and os.environ.get(key_env))
        result.append(
            {
                "key": key,
                "label": display,
                "base_url": base_url,
                "key_env": key_env,
                # Whether a usable key is already present in this process's env.
                "key_present": key_present,
                # Local runtimes (ollama) and generic OpenAI-compatible servers
                # need no hosted key; the UI shows "no key required".
                "local": key in _LOCAL_PROVIDERS or key_env is None,
                # Whether the user is expected to supply a custom endpoint URL.
                "needs_url": key in _LOCAL_PROVIDERS,
            }
        )
    return result


def models(provider: str) -> dict:
    """Return ``{"quick": [...], "deep": [...]}`` model options for a provider.

    OpenRouter is fetched live (like the CLI); providers without a static list
    fall back to a single custom-id entry.
    """
    provider = (provider or "").lower()

    if provider == "openrouter":
        fetched = _fetch_openrouter_models()[:50]
        fetched.append(("Custom model ID", "custom"))
        opts = _model_list(fetched)
        return {"quick": opts, "deep": opts}

    if provider not in MODEL_OPTIONS:
        # azure / unknown: model (deployment) is user-specified.
        custom = [{"id": "custom", "label": "Custom model ID", "custom": True}]
        return {"quick": custom, "deep": custom}

    return {
        "quick": _model_list(get_model_options(provider, "quick")),
        "deep": _model_list(get_model_options(provider, "deep")),
    }


def depths() -> list[dict]:
    """Return research-depth choices as ``[{label, value}]``."""
    return [{"label": label, "value": value} for label, value in DEPTH_OPTIONS]


def analysts(asset_type: str = "stock") -> list[dict]:
    """Return analyst options in canonical order, filtered for the asset type.

    Crypto has no fundamentals analyst (mirrors
    ``filter_analysts_for_asset_type``); reflect that by marking it available.
    """
    at = AssetType.CRYPTO if str(asset_type).lower() == "crypto" else AssetType.STOCK
    out = []
    for label, analyst in ANALYST_ORDER:
        if at == AssetType.CRYPTO and analyst == AnalystType.FUNDAMENTALS:
            continue
        out.append({"label": label, "value": analyst.value})
    return out


def default_analyst_values() -> list[str]:
    """The full default analyst set (all four), by wire value."""
    return [a.value for _, a in ANALYST_ORDER]


def requires_api_key(provider: str) -> bool:
    """Whether a provider requires a hosted API key to be set.

    Local runtimes (Ollama) and generic OpenAI-compatible servers (vLLM, LM
    Studio, llama.cpp) authenticate optionally or not at all, so they never
    require a key even though a key env var may be registered for keyed relays.
    Bedrock uses the AWS credential chain rather than a single key env.
    """
    provider = (provider or "").lower()
    if provider in _LOCAL_PROVIDERS:
        return False
    return get_api_key_env(provider) is not None


def form_defaults() -> dict:
    """Pre-selection defaults for the form, from DEFAULT_CONFIG.

    DEFAULT_CONFIG already absorbs the TRADINGAGENTS_LLM_PROVIDER /
    TRADINGAGENTS_QUICK_THINK_LLM / TRADINGAGENTS_DEEP_THINK_LLM /
    TRADINGAGENTS_LLM_BACKEND_URL env overrides (same as the CLI, which skips
    the matching interactive prompts). Surfacing them here lets the browser
    form open with the user's configured provider and model ids preselected
    instead of the first provider in the list.
    """
    return {
        "llm_provider": (DEFAULT_CONFIG.get("llm_provider") or "").lower() or None,
        "quick_think_llm": DEFAULT_CONFIG.get("quick_think_llm"),
        "deep_think_llm": DEFAULT_CONFIG.get("deep_think_llm"),
        "backend_url": DEFAULT_CONFIG.get("backend_url"),
    }


def catalog(asset_type: str = "stock") -> dict:
    """Full catalog payload for the front-end to build its form."""
    return {
        "providers": providers(),
        "depths": depths(),
        "analysts": analysts(asset_type),
        "default_analysts": default_analyst_values(),
        "defaults": form_defaults(),
        # When the server runs inside a container, the form prefills local
        # backend URLs with host.docker.internal instead of localhost so a
        # browser run reaches a model server on the Docker host.
        "in_docker": running_in_docker(),
    }


def asset_type_for(ticker: str) -> str:
    """Classify a ticker into ``"stock"``/``"crypto"`` via the shared detector."""
    return detect_asset_type(ticker).value
