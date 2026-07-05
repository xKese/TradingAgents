"""Unit tests for OAuth + MCP session establishment (MCP-T2).

No network: `streamablehttp_client`/`ClientSession` are monkeypatched with
async fakes so `connect()`/`close()` can be exercised deterministically on
`_AsyncWorker`'s real event loop. `_FileTokenStorage` and
`_parse_oauth_callback_request` are exercised directly (pure/file-only, no
sockets). The real handshake against `agent.robinhood.com` is exercised only
by the opt-in live test (`tests/ops/broker/test_robinhood_live.py`,
`OPS_RH_LIVE_TESTS=1`) — never here.
"""
from __future__ import annotations

import webbrowser

import anyio
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ops.broker import mcp_client
from ops.broker.mcp_client import (
    MCPUnavailable,
    OAuthClientProvider,
    RealRobinhoodMCPClient,
    _FileTokenStorage,
    _LocalhostOAuthCallback,
    _parse_oauth_callback_request,
)

# --- _FileTokenStorage round-trip -------------------------------------------


def test_get_tokens_returns_none_when_file_absent(tmp_path):
    storage = _FileTokenStorage(tmp_path / "nope.json")
    assert _run(storage.get_tokens()) is None


def test_get_client_info_returns_none_when_file_absent(tmp_path):
    storage = _FileTokenStorage(tmp_path / "nope.json")
    assert _run(storage.get_client_info()) is None


def test_set_then_get_tokens_round_trips(tmp_path):
    path = tmp_path / "token.json"
    storage = _FileTokenStorage(path)
    token = OAuthToken(access_token="abc123", refresh_token="r-1", expires_in=3600, scope="trade")

    _run(storage.set_tokens(token))
    fetched = _run(storage.get_tokens())

    assert fetched == token


def test_set_then_get_client_info_round_trips(tmp_path):
    path = tmp_path / "token.json"
    storage = _FileTokenStorage(path)
    info = OAuthClientInformationFull(
        redirect_uris=["http://127.0.0.1:51823/callback"],
        client_id="client-abc",
        client_secret=None,
    )

    _run(storage.set_client_info(info))
    fetched = _run(storage.get_client_info())

    assert fetched == info


def test_tokens_and_client_info_coexist_in_one_file(tmp_path):
    path = tmp_path / "token.json"
    storage = _FileTokenStorage(path)
    token = OAuthToken(access_token="abc123")
    info = OAuthClientInformationFull(redirect_uris=["http://127.0.0.1:51823/callback"], client_id="c1")

    _run(storage.set_tokens(token))
    _run(storage.set_client_info(info))

    assert _run(storage.get_tokens()) == token
    assert _run(storage.get_client_info()) == info


def test_set_tokens_preserves_0600_perms(tmp_path):
    path = tmp_path / "sub" / "token.json"
    storage = _FileTokenStorage(path)
    _run(storage.set_tokens(OAuthToken(access_token="abc123")))

    mode = path.stat().st_mode & 0o777
    assert mode == 0o600


def test_file_token_storage_uses_env_override_path(monkeypatch, tmp_path):
    override = tmp_path / "custom_token.json"
    monkeypatch.setenv("OPS_RH_TOKEN_PATH", str(override))

    from ops.broker.mcp_client import _resolve_token_path

    storage = _FileTokenStorage(_resolve_token_path())
    _run(storage.set_tokens(OAuthToken(access_token="abc123")))

    assert override.exists()
    assert override.stat().st_mode & 0o777 == 0o600


# --- _parse_oauth_callback_request (pure, no socket) ------------------------


def test_parse_oauth_callback_request_extracts_code_and_state():
    raw = b"GET /callback?code=abc123&state=xyz HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
    code, state = _parse_oauth_callback_request(raw)
    assert code == "abc123"
    assert state == "xyz"


def test_parse_oauth_callback_request_missing_state_is_none():
    raw = b"GET /callback?code=abc123 HTTP/1.1\r\n\r\n"
    code, state = _parse_oauth_callback_request(raw)
    assert code == "abc123"
    assert state is None


def test_parse_oauth_callback_request_malformed_returns_none_none():
    assert _parse_oauth_callback_request(b"garbage") == (None, None)


def test_localhost_callback_construction_does_no_io():
    callback = _LocalhostOAuthCallback(port=51823)
    assert callback.redirect_uri == "http://127.0.0.1:51823/callback"


def test_redirect_handler_opens_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    callback = _LocalhostOAuthCallback(port=51823)

    _run(callback.redirect_handler("https://robinhood.example/authorize?x=1"))

    assert opened == ["https://robinhood.example/authorize?x=1"]


# --- connect()/close(): fake transport + session, no network ---------------


class _FakeTransportCM:
    """Stand-in for the async CM `streamablehttp_client(...)` returns."""

    instances: list[_FakeTransportCM] = []

    def __init__(self, *, url, auth, fail_enter: bool = False):
        self.url = url
        self.auth = auth
        self.fail_enter = fail_enter
        self.entered = False
        self.exited = False
        _FakeTransportCM.instances.append(self)

    async def __aenter__(self):
        if self.fail_enter:
            raise ConnectionRefusedError("no route to host (fake)")
        self.entered = True
        return ("READ", "WRITE", lambda: "session-id")

    async def __aexit__(self, *exc_info):
        self.exited = True
        return False


class _FakeClientSession:
    """Stand-in for `mcp.ClientSession`."""

    instances: list[_FakeClientSession] = []

    def __init__(self, read, write, fail_initialize: bool = False):
        self.read = read
        self.write = write
        self.fail_initialize = fail_initialize
        self.initialized = False
        self.exited = False
        _FakeClientSession.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.exited = True
        return False

    async def initialize(self):
        if self.fail_initialize:
            raise RuntimeError("handshake rejected (fake)")
        self.initialized = True


@pytest.fixture(autouse=True)
def _reset_fake_registries():
    _FakeTransportCM.instances.clear()
    _FakeClientSession.instances.clear()
    yield
    _FakeTransportCM.instances.clear()
    _FakeClientSession.instances.clear()


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_connect_assembles_provider_and_sets_session(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_client, "streamablehttp_client", lambda *, url, auth: _FakeTransportCM(url=url, auth=auth)
    )
    monkeypatch.setattr(mcp_client, "ClientSession", _FakeClientSession)

    client = RealRobinhoodMCPClient(token_path=tmp_path / "token.json")
    try:
        client.connect()

        assert len(_FakeTransportCM.instances) == 1
        transport = _FakeTransportCM.instances[0]
        assert transport.url == client._endpoint
        assert isinstance(transport.auth, OAuthClientProvider)
        assert transport.auth.context.server_url == client._endpoint
        assert isinstance(transport.auth.context.storage, _FileTokenStorage)
        assert transport.auth.context.storage._path == tmp_path / "token.json"
        assert transport.entered is True

        assert len(_FakeClientSession.instances) == 1
        session = _FakeClientSession.instances[0]
        assert session.initialized is True
        assert client._session is session
    finally:
        client.close()


def test_connect_is_idempotent_when_already_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_client, "streamablehttp_client", lambda *, url, auth: _FakeTransportCM(url=url, auth=auth)
    )
    monkeypatch.setattr(mcp_client, "ClientSession", _FakeClientSession)

    client = RealRobinhoodMCPClient(token_path=tmp_path / "token.json")
    try:
        client.connect()
        client.connect()
        assert len(_FakeTransportCM.instances) == 1
        assert len(_FakeClientSession.instances) == 1
    finally:
        client.close()


def test_connect_transport_failure_maps_to_mcp_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_client,
        "streamablehttp_client",
        lambda *, url, auth: _FakeTransportCM(url=url, auth=auth, fail_enter=True),
    )
    monkeypatch.setattr(mcp_client, "ClientSession", _FakeClientSession)

    client = RealRobinhoodMCPClient(token_path=tmp_path / "token.json")
    try:
        with pytest.raises(MCPUnavailable):
            client.connect()
        assert client._session is None
    finally:
        client.close()


def test_connect_session_initialize_failure_closes_transport_and_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_client, "streamablehttp_client", lambda *, url, auth: _FakeTransportCM(url=url, auth=auth)
    )
    monkeypatch.setattr(
        mcp_client,
        "ClientSession",
        lambda read, write: _FakeClientSession(read, write, fail_initialize=True),
    )

    client = RealRobinhoodMCPClient(token_path=tmp_path / "token.json")
    try:
        with pytest.raises(MCPUnavailable):
            client.connect()

        assert client._session is None
        # Partial progress (transport entered) must be torn down, not leaked.
        assert _FakeTransportCM.instances[0].exited is True
    finally:
        client.close()


def test_close_tears_down_session_and_transport_before_stopping_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(
        mcp_client, "streamablehttp_client", lambda *, url, auth: _FakeTransportCM(url=url, auth=auth)
    )
    monkeypatch.setattr(mcp_client, "ClientSession", _FakeClientSession)

    client = RealRobinhoodMCPClient(token_path=tmp_path / "token.json")
    client.connect()
    transport = _FakeTransportCM.instances[0]
    session = _FakeClientSession.instances[0]

    client.close()

    # If close() stopped the worker before signaling _serve()'s shutdown
    # event and waiting for it to finish, these would stay False.
    assert session.exited is True
    assert transport.exited is True
    assert client._worker is None
    assert client._lifetime is None


def test_close_is_idempotent_and_safe_without_connect(tmp_path):
    client = RealRobinhoodMCPClient(token_path=tmp_path / "token.json")
    client.close()
    client.close()


# --- Task-affinity regression (MCP-T2 Finding 1) ----------------------------
#
# The fakes above (`_FakeTransportCM`/`_FakeClientSession`) don't create real
# anyio task groups, so they can't catch a connect/close design that splits
# entering and exiting a cancel scope across two different asyncio Tasks —
# anyio only raises when a *real* task-affine cancel scope is exited from a
# Task other than the one that entered it. `_TaskAffineCM` below enters a
# real `anyio.create_task_group()` in `__aenter__` and exits it in
# `__aexit__`, reproducing that constraint. Verified against real anyio in a
# standalone scratch script: the old connect()-submits-one-coroutine /
# close()-submits-a-second-coroutine shape raises
# `RuntimeError("Attempted to exit cancel scope in a different task than it
# was entered in")` on close(); the single-Task `_serve()` design does not.


class _TaskAffineCM:
    """Enters/exits a real anyio cancel scope — task-affine like the SDK's."""

    def __init__(self) -> None:
        self._tg = None

    async def __aenter__(self):
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()
        return self

    async def __aexit__(self, *exc_info):
        return await self._tg.__aexit__(*exc_info)


class _TaskAffineTransportCM(_TaskAffineCM):
    """Stand-in for `streamablehttp_client(...)`, task-affine like the real one."""

    def __init__(self, *, url, auth) -> None:
        super().__init__()
        self.url = url
        self.auth = auth

    async def __aenter__(self):
        await super().__aenter__()
        return ("READ", "WRITE", lambda: "session-id")


class _TaskAffineSession(_TaskAffineCM):
    """Stand-in for `mcp.ClientSession`, task-affine like the real one."""

    def __init__(self, read, write) -> None:
        super().__init__()
        self.read = read
        self.write = write

    async def __aenter__(self):
        await super().__aenter__()
        return self

    async def initialize(self) -> None:
        pass


def test_connect_then_close_survives_real_anyio_task_affinity(monkeypatch, tmp_path):
    """Regression for the connect/close-split-across-Tasks bug.

    Under the old design, connect() entered these CMs on one asyncio Task
    (submitted via `_worker.submit`) and close() exited them on a second,
    different Task — anyio's real cancel-scope task-affinity check turns
    that into an uncaught RuntimeError, which `close()`'s
    `suppress(MCPUnavailable)` does not catch, so `self._worker.stop()` is
    never reached (leaked daemon thread). The fix runs the whole connection
    lifetime — enter through exit — on a single long-lived Task, so this
    must complete cleanly and leave the worker thread stopped.
    """
    monkeypatch.setattr(
        mcp_client, "streamablehttp_client",
        lambda *, url, auth: _TaskAffineTransportCM(url=url, auth=auth),
    )
    monkeypatch.setattr(mcp_client, "ClientSession", _TaskAffineSession)

    client = RealRobinhoodMCPClient(token_path=tmp_path / "token.json")
    client.connect()
    worker_thread = client._worker._thread

    client.close()

    assert worker_thread is not None
    assert not worker_thread.is_alive()
    assert client._session is None


# --- L1: callback server survives stray connections -------------------------


def _free_port() -> int:
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_wait_for_callback_survives_stray_connection_before_real_callback():
    """L1: browsers open speculative connections and probe /favicon.ico; a
    single-accept server that trusts the first connection fails the whole
    OAuth flow on such a stray. The server must respond 404 to requests
    without a code and keep listening until the real callback arrives."""
    import asyncio

    from ops.broker.mcp_client import _LocalhostOAuthCallback

    port = _free_port()
    cb = _LocalhostOAuthCallback(port=port)

    async def _http_get(path: str) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read(1024)
        writer.close()
        return data

    async def scenario():
        task = asyncio.ensure_future(cb.wait_for_callback())
        await asyncio.sleep(0.05)  # let the server bind + listen
        stray = await _http_get("/favicon.ico")          # no code param
        real = await _http_get("/callback?code=abc&state=xyz")
        code, state = await asyncio.wait_for(task, timeout=2)
        return code, state, stray, real

    code, state, stray, real = asyncio.run(scenario())
    assert (code, state) == ("abc", "xyz")
    assert b"404" in stray
    assert b"200" in real
