"""Tests for the provider-gated sampling seed.

Seed is only supported by OpenAI-compatible Chat Completions and Azure:
_get_provider_kwargs drops it for anthropic/google/bedrock, and the OpenAI
client drops it when the native Responses API is active (no seed param there).
"""

import importlib

import pytest

from tradingagents.llm_clients.factory import create_llm_client


@pytest.mark.unit
class TestProviderKwargsSeed:
    """_get_provider_kwargs int-coerces and forwards seed, gated per provider."""

    def _kwargs_for(self, provider, seed):
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        # Call the method without constructing the full graph.
        graph = TradingAgentsGraph.__new__(TradingAgentsGraph)
        graph.config = {"llm_provider": provider, "seed": seed}
        return TradingAgentsGraph._get_provider_kwargs(graph)

    @pytest.mark.parametrize(
        "provider", ["openai", "azure", "deepseek", "groq", "ollama"]
    )
    def test_seed_forwarded_for_supported_providers(self, provider):
        assert self._kwargs_for(provider, 7)["seed"] == 7

    def test_env_string_int_coerced(self):
        assert self._kwargs_for("openai", "42")["seed"] == 42

    @pytest.mark.parametrize("provider", ["anthropic", "google", "bedrock"])
    def test_seed_dropped_for_unsupported_providers(self, provider):
        assert "seed" not in self._kwargs_for(provider, 7)

    def test_none_omitted(self):
        assert "seed" not in self._kwargs_for("openai", None)

    def test_empty_string_omitted(self):
        assert "seed" not in self._kwargs_for("openai", "")


@pytest.mark.unit
class TestSeedEnvOverlay:
    def test_env_sets_seed(self, monkeypatch):
        import tradingagents.default_config as dc
        monkeypatch.setenv("TRADINGAGENTS_SEED", "42")
        importlib.reload(dc)
        # Stored on config (string from env is fine; consumed via int()).
        assert int(dc.DEFAULT_CONFIG["seed"]) == 42
        monkeypatch.delenv("TRADINGAGENTS_SEED", raising=False)
        importlib.reload(dc)

    def test_default_seed_is_none(self, monkeypatch):
        import tradingagents.default_config as dc
        monkeypatch.delenv("TRADINGAGENTS_SEED", raising=False)
        importlib.reload(dc)
        assert dc.DEFAULT_CONFIG["seed"] is None


@pytest.mark.unit
class TestSeedClientForwarding:
    def test_openai_compatible_forwards_seed(self):
        llm = create_llm_client(
            provider="deepseek", model="deepseek-chat", seed=7, api_key="placeholder"
        ).get_llm()
        assert llm.seed == 7

    def test_azure_forwards_seed(self, monkeypatch):
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "placeholder")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
        monkeypatch.setenv("OPENAI_API_VERSION", "2025-03-01-preview")
        llm = create_llm_client(
            provider="azure", model="gpt-4.1", seed=7, api_key="placeholder"
        ).get_llm()
        assert llm.seed == 7

    def test_native_openai_responses_api_drops_seed(self):
        # Native OpenAI (no custom base_url) runs the Responses API, which has
        # no seed parameter — forwarding it would 400 the request.
        with pytest.warns(UserWarning, match="Responses API"):
            llm = create_llm_client(
                provider="openai", model="gpt-4.1", seed=7, api_key="placeholder"
            ).get_llm()
        assert llm.use_responses_api is True
        assert getattr(llm, "seed", None) is None

    def test_openai_custom_base_url_keeps_seed(self):
        # A proxy/gateway/local server on the openai provider speaks Chat
        # Completions, where seed is valid.
        llm = create_llm_client(
            provider="openai",
            model="gpt-4.1",
            base_url="http://localhost:8000/v1",
            seed=7,
            api_key="placeholder",
        ).get_llm()
        assert llm.seed == 7

    def test_no_seed_leaves_default(self):
        llm = create_llm_client(
            provider="deepseek", model="deepseek-chat", api_key="placeholder"
        ).get_llm()
        assert getattr(llm, "seed", None) is None
