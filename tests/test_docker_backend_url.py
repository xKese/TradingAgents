"""In Docker, a loopback backend URL points at the container, not the host
running the model server; it must be rewritten to host.docker.internal so a
run started with the default localhost URL still connects (#web-lmstudio)."""

from __future__ import annotations

import pytest

from tradingagents import runtime
from tradingagents.runtime import rewrite_loopback_for_docker, running_in_docker


@pytest.fixture(autouse=True)
def _clear_docker_cache():
    """running_in_docker() is lru-cached; reset it around each test."""
    running_in_docker.cache_clear()
    yield
    running_in_docker.cache_clear()


def _force_docker(monkeypatch, on: bool):
    monkeypatch.setenv("TRADINGAGENTS_IN_DOCKER", "true" if on else "false")
    running_in_docker.cache_clear()


@pytest.mark.unit
def test_not_in_docker_leaves_url_untouched(monkeypatch):
    _force_docker(monkeypatch, False)
    assert rewrite_loopback_for_docker("http://localhost:1234/v1") == "http://localhost:1234/v1"


@pytest.mark.unit
def test_localhost_rewritten_in_docker(monkeypatch):
    _force_docker(monkeypatch, True)
    assert (
        rewrite_loopback_for_docker("http://localhost:1234/v1")
        == "http://host.docker.internal:1234/v1"
    )


@pytest.mark.unit
def test_loopback_ip_rewritten_in_docker(monkeypatch):
    _force_docker(monkeypatch, True)
    assert (
        rewrite_loopback_for_docker("http://127.0.0.1:11434/v1")
        == "http://host.docker.internal:11434/v1"
    )


@pytest.mark.unit
def test_port_and_path_preserved(monkeypatch):
    _force_docker(monkeypatch, True)
    assert (
        rewrite_loopback_for_docker("https://localhost:8000/openai/v1")
        == "https://host.docker.internal:8000/openai/v1"
    )


@pytest.mark.unit
def test_remote_host_untouched_in_docker(monkeypatch):
    _force_docker(monkeypatch, True)
    url = "https://api.openai.com/v1"
    assert rewrite_loopback_for_docker(url) == url


@pytest.mark.unit
def test_already_docker_host_untouched(monkeypatch):
    _force_docker(monkeypatch, True)
    url = "http://host.docker.internal:1234/v1"
    assert rewrite_loopback_for_docker(url) == url


@pytest.mark.unit
def test_none_and_empty_passthrough(monkeypatch):
    _force_docker(monkeypatch, True)
    assert rewrite_loopback_for_docker(None) is None
    assert rewrite_loopback_for_docker("") == ""


@pytest.mark.unit
def test_openai_client_applies_rewrite_in_docker(monkeypatch):
    """End-to-end: the openai_compatible client resolves a docker-safe base_url."""
    _force_docker(monkeypatch, True)
    from tradingagents.llm_clients.openai_client import OpenAIClient

    llm = OpenAIClient(
        "local-model",
        base_url="http://localhost:1234/v1",
        provider="openai_compatible",
    ).get_llm()
    # langchain-openai stores the resolved endpoint on openai_api_base.
    assert str(llm.openai_api_base) == "http://host.docker.internal:1234/v1"


@pytest.mark.unit
def test_openai_client_no_rewrite_outside_docker(monkeypatch):
    _force_docker(monkeypatch, False)
    from tradingagents.llm_clients.openai_client import OpenAIClient

    llm = OpenAIClient(
        "local-model",
        base_url="http://localhost:1234/v1",
        provider="openai_compatible",
    ).get_llm()
    assert str(llm.openai_api_base) == "http://localhost:1234/v1"


@pytest.mark.unit
def test_env_override_false_disables_detection(monkeypatch):
    """TRADINGAGENTS_IN_DOCKER=false wins even if /.dockerenv exists."""
    monkeypatch.setattr(runtime.os.path, "exists", lambda p: True)
    monkeypatch.setenv("TRADINGAGENTS_IN_DOCKER", "false")
    running_in_docker.cache_clear()
    assert running_in_docker() is False
