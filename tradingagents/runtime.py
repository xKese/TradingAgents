"""Runtime-environment helpers (container detection, host rewriting).

Kept dependency-free and separate from ``dataflows.utils`` (which pulls in
pandas) so the LLM client layer can import it cheaply on every run.
"""

from __future__ import annotations

import os
import warnings
from functools import lru_cache
from urllib.parse import urlparse, urlunparse

_BOOL_TRUE = ("true", "1", "yes", "on")

# Hosts that, inside a container, point at the container itself rather than the
# Docker host. A local model server (LM Studio, Ollama, vLLM) started on the
# host is unreachable via these from within the container.
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")

# Docker Desktop (and the ``host.docker.internal:host-gateway`` mapping in
# docker-compose.yml) exposes the host under this name.
DOCKER_HOST_ALIAS = "host.docker.internal"


@lru_cache(maxsize=1)
def running_in_docker() -> bool:
    """Whether this process is running inside a Docker/OCI container.

    Detection order:
    1. ``TRADINGAGENTS_IN_DOCKER`` env override (truthy) — lets users force the
       behavior on runtimes where the heuristics below don't apply.
    2. ``/.dockerenv`` — present in Docker containers.

    Cached: the environment doesn't change during a process's lifetime.
    """
    override = os.environ.get("TRADINGAGENTS_IN_DOCKER")
    if override is not None and override.strip():
        return override.strip().lower() in _BOOL_TRUE
    return os.path.exists("/.dockerenv")


# URLs already warned about, so the rewrite notice fires once per distinct
# endpoint rather than on every LLM client construction within a run.
_warned_rewrites: set[str] = set()


def rewrite_loopback_for_docker(base_url: str | None) -> str | None:
    """Rewrite a loopback host to ``host.docker.internal`` when in a container.

    Inside a container ``http://localhost:1234/v1`` points at the container
    itself, so a local model server (LM Studio, Ollama, vLLM) running on the
    Docker host is unreachable and the OpenAI SDK raises
    ``APIConnectionError``. Docker Desktop (and the ``host-gateway`` mapping in
    docker-compose.yml) exposes the host as ``host.docker.internal``; rewrite
    the host to that so a browser/CLI run started with the default
    ``localhost`` URL still reaches the host server.

    Only the host is changed — scheme, port, and path are preserved. Non-loopback
    URLs (real remotes, already-``host.docker.internal`` URLs) and non-container
    runtimes are returned unchanged. Emits a one-time ``RuntimeWarning`` per URL.
    """
    if not base_url or not running_in_docker():
        return base_url

    # ``urlparse`` needs a scheme to populate ``hostname``; a bare
    # "localhost:1234" parses with the host in ``path`` instead.
    to_parse = base_url if "://" in base_url else "http://" + base_url
    parsed = urlparse(to_parse)
    if (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        return base_url

    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    netloc = f"{userinfo}{DOCKER_HOST_ALIAS}"
    if parsed.port:
        netloc += f":{parsed.port}"
    rewritten = urlunparse(parsed._replace(netloc=netloc))

    if base_url not in _warned_rewrites:
        _warned_rewrites.add(base_url)
        warnings.warn(
            f"Running in Docker: rewrote backend URL host from a loopback "
            f"address to '{DOCKER_HOST_ALIAS}' so the container can reach the "
            f"model server on the host ({base_url} -> {rewritten}). Set "
            f"TRADINGAGENTS_IN_DOCKER=false to disable this.",
            RuntimeWarning,
            stacklevel=2,
        )
    return rewritten
