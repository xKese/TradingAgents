"""Unit tests for per-stage research model specs."""

import pytest

from ops.research.models import ModelSpec, build_stage_llm, parse_model_spec

pytestmark = pytest.mark.unit


def test_parses_provider_model():
    assert parse_model_spec("anthropic:claude-sonnet-5") == ModelSpec(
        provider="anthropic", model="claude-sonnet-5", base_url=None,
    )


def test_parses_provider_model_url():
    spec = parse_model_spec("openai_compatible:deepseek-v4-flash@http://127.0.0.1:8000/v1")
    assert spec.provider == "openai_compatible"
    assert spec.model == "deepseek-v4-flash"
    assert spec.base_url == "http://127.0.0.1:8000/v1"


@pytest.mark.parametrize("bad", ["", "no-colon", ":model", "provider:", "p:m@"])
def test_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_model_spec(bad)


def test_build_stage_llm_routes_through_registry(monkeypatch):
    captured = {}

    class FakeClient:
        def get_llm(self):
            return "the-llm"

    def fake_create(*, provider, model, base_url=None, **kw):
        captured.update(provider=provider, model=model, base_url=base_url)
        return FakeClient()

    import tradingagents.llm_clients as llm_clients

    monkeypatch.setattr(llm_clients, "create_llm_client", fake_create)
    llm = build_stage_llm("openai_compatible:foo@http://localhost:1234/v1")
    assert llm == "the-llm"
    assert captured == {
        "provider": "openai_compatible", "model": "foo",
        "base_url": "http://localhost:1234/v1",
    }
