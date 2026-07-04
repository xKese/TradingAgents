"""RobinhoodMCPClient protocol + typed DTOs.

Concrete implementations:
- RealRobinhoodMCPClient — production, wraps the mcp Python SDK.
- FakeMCPClient (tests/ops/broker/fakes.py) — in-memory, deterministic.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import socket
import threading
import webbrowser
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Coroutine, Protocol, runtime_checkable
from urllib.parse import parse_qs, urlparse

from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken

from ops.broker.types import OrderType, Side


class MCPUnavailable(Exception):
    """Raised when the MCP endpoint fails (network, auth, protocol error, or timeout)."""


class MCPProtocolError(Exception):
    """Raised when an MCP response doesn't match the expected shape.

    Distinguished from `MCPUnavailable`: this is a parse/shape mismatch (a
    renamed field, a missing key, an unexpected type) against a server that
    *did* respond — not a transport/timeout/auth failure. Callers should not
    treat this as "the broker is unreachable, retry later."
    """


@dataclass(frozen=True)
class AccountInfo:
    cash: Decimal
    equity: Decimal
    buying_power: Decimal


@dataclass(frozen=True)
class MCPPosition:
    symbol: str
    quantity: Decimal
    avg_price: Decimal


@dataclass(frozen=True)
class MCPOrderAck:
    order_id: str
    client_order_id: str
    symbol: str
    side: Side
    quantity: Decimal | None
    notional: Decimal | None
    status: str    # "queued" | "filled" | "rejected"
    fill_price: Decimal | None


@runtime_checkable
class RobinhoodMCPClient(Protocol):
    def get_account(self) -> AccountInfo: ...
    def get_positions(self) -> list[MCPPosition]: ...
    def get_quote(self, symbol: str) -> Decimal: ...
    def place_equity_order(
        self, *, symbol: str, side: Side,
        notional: Decimal | None, quantity: Decimal | None,
        order_type: OrderType, limit_price: Decimal | None,
        client_order_id: str,
    ) -> MCPOrderAck: ...
    def cancel_equity_order(self, order_id: str) -> None: ...


# --- Token file helpers -----------------------------------------------------
#
# Token cached on disk so the OAuth browser flow only runs on first use.
# Default location: ~/.config/tradingagents/robinhood_token.json
# Override via OPS_RH_TOKEN_PATH (used by tests to avoid touching $HOME).


def _resolve_token_path() -> Path:
    override = os.environ.get("OPS_RH_TOKEN_PATH")
    if override:
        return Path(override)
    home = Path(os.environ.get("HOME", "~")).expanduser()
    return home / ".config" / "tradingagents" / "robinhood_token.json"


def _write_token(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write with strict perms from creation — avoid a window where the
    # token file is briefly world/group readable.
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
    except Exception:
        # If the file existed with laxer perms, tighten before we bail.
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass
        raise


def _read_token(path: Path) -> dict | None:
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


# --- _FileTokenStorage --------------------------------------------------------
#
# Implements the real SDK's `mcp.client.auth.TokenStorage` Protocol (verified
# via `inspect.getsource`: async `get_tokens`/`set_tokens`/`get_client_info`/
# `set_client_info`, typed `OAuthToken | None` / `OAuthClientInformationFull |
# None`). Both the bearer token and the dynamic-client-registration info the
# SDK needs to resume without re-registering are persisted in the *same*
# on-disk JSON payload the pre-existing `_read_token`/`_write_token` helpers
# already manage (0600 perms, `OPS_RH_TOKEN_PATH` override), as two top-level
# keys: `{"token": {...}, "client_info": {...}}`. Either key may be absent.
#
# `model_dump(mode="json")` is used (not the default python mode) because
# `OAuthClientInformationFull` carries pydantic `AnyHttpUrl` fields (e.g.
# `client_uri`) that `json.dump` cannot serialize directly; `mode="json"`
# renders them as plain strings, which `model_validate` parses back on read.


class _FileTokenStorage(TokenStorage):
    """`TokenStorage` backed by the token file at `path`."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def get_tokens(self) -> OAuthToken | None:
        payload = _read_token(self._path)
        if not payload or "token" not in payload:
            return None
        return OAuthToken.model_validate(payload["token"])

    async def set_tokens(self, tokens: OAuthToken) -> None:
        payload = _read_token(self._path) or {}
        payload["token"] = tokens.model_dump(mode="json", exclude_none=True)
        _write_token(self._path, payload)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        payload = _read_token(self._path)
        if not payload or "client_info" not in payload:
            return None
        return OAuthClientInformationFull.model_validate(payload["client_info"])

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        payload = _read_token(self._path) or {}
        payload["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        _write_token(self._path, payload)


# --- OAuth browser + localhost callback ---------------------------------------
#
# The SDK's `OAuthClientProvider` only invokes `redirect_handler`/
# `callback_handler` when an authorization-code grant is actually needed (no
# cached/refreshable token) — verified by reading
# `mcp.client.auth.oauth2.OAuthContext._perform_authorization_code_grant`.
# Construction of `_LocalhostOAuthCallback` below does no I/O; the socket is
# opened lazily inside `wait_for_callback`, which only ever runs on the
# worker loop, and only when the SDK decides a fresh grant is required — i.e.
# never in the default unit-test suite, which fakes out the transport before
# any real `auth_flow` executes. It is exercised for real only by the opt-in
# live tests (Task 6).
#
# A fixed localhost port is used rather than an OS-assigned ephemeral one so
# that the redirect_uri baked into the OAuth client metadata (built once, at
# provider-construction time) doesn't need to know about a socket that isn't
# bound until later. Dynamic client registration means no external
# pre-registration of this redirect_uri is required.

_OAUTH_CALLBACK_PORT = 51823

_OAUTH_CALLBACK_RESPONSE_BODY = (
    b"<html><body>Robinhood MCP authorization complete. You may close this tab.</body></html>"
)


def _parse_oauth_callback_request(request: bytes) -> tuple[str | None, str | None]:
    """Extract `code` and `state` query params from a raw HTTP GET request.

    Pure/no I/O so it can be unit-tested without a real socket. `request` is
    the raw bytes read off the accepted connection, e.g.
    `b"GET /callback?code=abc&state=xyz HTTP/1.1\\r\\n..."`.
    """
    request_line = request.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    parts = request_line.split(" ")
    if len(parts) < 2:
        return None, None
    query = parse_qs(urlparse(parts[1]).query)
    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]
    return code, state


class _LocalhostOAuthCallback:
    """Supplies `OAuthClientProvider`'s `redirect_handler`/`callback_handler`.

    `redirect_handler` opens the authorization URL in the user's browser;
    `callback_handler` accepts exactly one localhost connection carrying the
    `code`/`state` query params and returns them. Construction is side-effect
    free; the listening socket is opened only inside `wait_for_callback`.
    """

    def __init__(self, port: int = _OAUTH_CALLBACK_PORT) -> None:
        self.port = port

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.port}/callback"

    async def redirect_handler(self, authorization_url: str) -> None:
        webbrowser.open(authorization_url)

    async def wait_for_callback(self) -> tuple[str, str | None]:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", self.port))
        sock.listen(1)
        sock.setblocking(False)
        try:
            conn, _addr = await loop.sock_accept(sock)
            try:
                data = await loop.sock_recv(conn, 65536)
                code, state = _parse_oauth_callback_request(data)
                await loop.sock_sendall(
                    conn,
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/html\r\n"
                    b"Content-Length: " + str(len(_OAUTH_CALLBACK_RESPONSE_BODY)).encode("ascii") + b"\r\n"
                    b"Connection: close\r\n\r\n" + _OAUTH_CALLBACK_RESPONSE_BODY,
                )
            finally:
                conn.close()
        finally:
            sock.close()
        if not code:
            raise MCPUnavailable("OAuth callback did not include an authorization code")
        return code, state


def _oauth_client_metadata(redirect_uri: str) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        redirect_uris=[redirect_uri],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        client_name="tradingagents-ops-robinhood-mcp",
    )


# --- _AsyncWorker -------------------------------------------------------------
#
# The installed `mcp` SDK (1.28.1) is async-only, and its transports use
# anyio cancel scopes that are bound to the asyncio Task that entered them.
# A per-call `asyncio.run(...)` (or `loop.run_until_complete(...)` invoked
# repeatedly on a stored-but-idle loop) runs each call in a *new* Task, so a
# transport/session entered under one Task cannot be used from another —
# anyio raises. The fix is structural: own exactly one event loop on one
# background thread for the client's entire lifetime, and always schedule
# coroutines onto that same loop via `run_coroutine_threadsafe`, so every
# coroutine (session open in Task 2, and every tool call) runs as a Task on
# the *same* loop, in the *same* thread, satisfying anyio's task affinity.


class _AsyncWorker:
    """Owns one asyncio event loop on one daemon thread, for a client's lifetime.

    Sync callers submit coroutines (created on the calling thread, but never
    awaited there) via `submit()`; they are scheduled onto the worker loop
    with `asyncio.run_coroutine_threadsafe` and actually run on the worker
    thread. This keeps every coroutine that ever touches the MCP session
    bound to a single loop/thread, which is required for the SDK's anyio-based
    transports (see module note above).
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn the daemon thread and block until its loop is running."""
        if self._thread is not None:
            return  # already started
        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        thread = threading.Thread(target=_run, daemon=True, name="mcp-async-worker")
        thread.start()
        ready.wait()
        self._thread = thread

    def submit(self, coro: Coroutine, *, timeout: float):
        """Schedule `coro` on the worker loop and block for its result.

        `coro` must be created by the caller but is only ever awaited on the
        worker thread's loop (never on the calling thread) — that hand-off is
        exactly what `run_coroutine_threadsafe` is for.

        On timeout, the future (not the loop) is cancelled: the worker loop
        keeps running and remains usable for subsequent submits.
        """
        if self._loop is None or self._thread is None or not self._thread.is_alive():
            coro.close()  # avoid a "coroutine was never awaited" warning
            raise MCPUnavailable("MCP worker is not running")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            fut.cancel()
            raise MCPUnavailable(f"MCP call timed out after {timeout}s") from None

    def stop(self) -> None:
        """Stop the loop and join the thread. Idempotent; safe if never started."""
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5.0)
        if self._loop is not None:
            self._loop.close()
        self._loop = None
        self._thread = None


# --- RealRobinhoodMCPClient --------------------------------------------------

_RH_MCP_ENDPOINT = "https://agent.robinhood.com/mcp/trading"


class RealRobinhoodMCPClient:
    """Concrete MCP client. Handles token load/refresh + OAuth on first run.

    Construction is side-effect-free (no network, no filesystem writes) so
    factories can build one at import time; the OAuth flow and MCP session
    are established lazily on the first call that needs them, via connect().

    SDK note (mcp==1.28.1): the installed `mcp` Python SDK is async-only —
    `mcp.client.session.ClientSession.call_tool` is a coroutine, and the
    transports (`mcp.client.streamable_http.streamablehttp_client`,
    `sse_client`, etc.) are async context managers that must be entered
    before a session can be used. There is no sync ClientSession in this
    SDK version.

    This class exposes the RobinhoodMCPClient Protocol's *sync* methods, so
    each protocol method bridges into the SDK via `_call_tool`. The bridge is
    a long-lived `_AsyncWorker`: one daemon thread owns one asyncio event
    loop for this client's entire lifetime, and every coroutine (session
    open, every tool call) is scheduled onto that same loop via
    `asyncio.run_coroutine_threadsafe`. This is required because the SDK's
    transports use anyio cancel scopes bound to the asyncio Task that entered
    them — a per-call `asyncio.run(...)` (or repeated `run_until_complete` on
    an otherwise-idle stored loop) creates a new Task each time, so a
    transport/session entered in one call can't be reused from the next.
    Routing every call through the same worker thread's loop keeps it all on
    one Task-affine loop. See `_AsyncWorker` for the mechanics.

    `connect()` opens the real transport/session: `streamablehttp_client`
    (auth=`OAuthClientProvider`, backed by `_FileTokenStorage`) then
    `ClientSession`, both entered via an `AsyncExitStack` on the worker
    loop and kept open (not exited) for the client's lifetime — `close()`
    tears them down, on that same loop, before stopping it. OAuth itself
    (browser flow on first run, cached token thereafter) is handled
    transparently by `OAuthClientProvider`, not by manual code here — see
    `_LocalhostOAuthCallback`.
    """

    def __init__(
        self,
        *,
        endpoint: str = _RH_MCP_ENDPOINT,
        token_path: Path | None = None,
        connect_timeout: float = 120.0,
    ):
        self._endpoint = endpoint
        self._token_path = token_path or _resolve_token_path()
        self._connect_timeout = connect_timeout
        self._session = None  # populated on connect(); see class docstring
        self._worker: _AsyncWorker | None = None  # started lazily; see connect()
        self._exit_stack: AsyncExitStack | None = None  # populated on connect()

    def connect(self) -> None:
        """Establish the MCP session (OAuth + transport + initialize).

        Starts this client's `_AsyncWorker` if needed, then submits a single
        coroutine — on the worker loop — that assembles
        `streamablehttp_client(url=..., auth=OAuthClientProvider(...))` and
        `ClientSession`, entering both via an `AsyncExitStack`. The stack is
        *not* exited here: the transport/session must stay bound to the same
        asyncio Task/loop they were entered on (see `_AsyncWorker`'s module
        note), so they are kept alive on `self._exit_stack` until `close()`
        tears them down on that same loop.

        `OAuthClientProvider`'s `redirect_handler`/`callback_handler` (see
        `_LocalhostOAuthCallback`) run the browser-based authorization-code
        grant transparently, but only if the cached token in
        `_FileTokenStorage` is absent/unrefreshable — there is no separate
        manual flow.

        Any transport/handshake failure (auth error, connection refused,
        timeout, a rejected `initialize()`) is mapped to `MCPUnavailable`;
        partial progress (e.g. transport opened but `initialize()` failed) is
        torn down via the same `AsyncExitStack` before re-raising, so no
        connection is leaked.

        Idempotent: a second call while already connected is a no-op.
        """
        if self._worker is None:
            self._worker = _AsyncWorker()
            self._worker.start()
        if self._session is not None:
            return

        async def _connect() -> None:
            callback = _LocalhostOAuthCallback()
            provider = OAuthClientProvider(
                server_url=self._endpoint,
                client_metadata=_oauth_client_metadata(callback.redirect_uri),
                storage=_FileTokenStorage(self._token_path),
                redirect_handler=callback.redirect_handler,
                callback_handler=callback.wait_for_callback,
            )
            stack = AsyncExitStack()
            try:
                read, write, _get_session_id = await stack.enter_async_context(
                    streamablehttp_client(url=self._endpoint, auth=provider)
                )
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
            except Exception:
                await stack.aclose()
                raise
            self._exit_stack = stack
            self._session = session

        try:
            self._worker.submit(_connect(), timeout=self._connect_timeout)
        except MCPUnavailable:
            raise
        except Exception as exc:
            raise MCPUnavailable(f"MCP handshake failed: {exc}") from exc

    def close(self) -> None:
        """Tear down the MCP session, then stop the worker thread/loop.

        Order matters: `self._exit_stack` (the transport + session's async
        context managers) is closed via a coroutine submitted to the worker
        — i.e. run on the same loop/thread they were entered on — BEFORE the
        worker is stopped. Closing them after the loop stops would either
        hang (nothing left to run the coroutine) or silently leak the
        connection. Idempotent; safe if `connect()` was never called.
        """
        if self._worker is not None and self._exit_stack is not None:
            stack, self._exit_stack = self._exit_stack, None
            self._session = None
            # Best-effort teardown; still proceed to stop the worker either way.
            with suppress(MCPUnavailable):
                self._worker.submit(stack.aclose(), timeout=self._connect_timeout)
        if self._worker is not None:
            self._worker.stop()
            self._worker = None

    def _call_tool(self, name: str, arguments: dict, *, timeout: float = 30.0) -> dict:
        """Sync bridge to the SDK's async `ClientSession.call_tool`.

        Submits the coroutine to this client's `_AsyncWorker` (started in
        `connect()`, or lazily here if a test/caller set `_session` without
        going through `connect()` first — matching the existing
        `if self._session is None: self.connect()` lazy pattern used by
        every Protocol method), which runs it on the worker thread's single
        long-lived event loop and blocks this calling thread for the
        result. Unpacks the real SDK response shape
        (`mcp.types.CallToolResult`), preferring `structuredContent` when
        the server provides it.
        """
        async def _call() -> dict:
            result = await self._session.call_tool(name, arguments)
            if result.isError:
                raise MCPUnavailable(f"MCP tool '{name}' returned an error result")
            if result.structuredContent is not None:
                return result.structuredContent
            return {"content": result.content}

        if self._worker is None:
            self._worker = _AsyncWorker()
            self._worker.start()
        return self._worker.submit(_call(), timeout=timeout)

    # Protocol methods delegate to the MCP session with narrow try/except
    # wrapping to MCPUnavailable.
    def get_account(self) -> AccountInfo:
        if self._session is None:
            self.connect()
        try:
            result = self._call_tool("get_accounts", {})
            row = result["accounts"][0]
            return AccountInfo(
                cash=Decimal(str(row["cash"])),
                equity=Decimal(str(row["equity"])),
                buying_power=Decimal(str(row.get("buying_power", row["cash"]))),
            )
        except Exception as exc:
            raise MCPUnavailable(f"get_account failed: {exc}") from exc

    def get_positions(self) -> list[MCPPosition]:
        if self._session is None:
            self.connect()
        try:
            result = self._call_tool("get_equity_positions", {})
            return [
                MCPPosition(
                    symbol=row["symbol"],
                    quantity=Decimal(str(row["quantity"])),
                    avg_price=Decimal(str(row["average_price"])),
                )
                for row in result.get("positions", [])
            ]
        except Exception as exc:
            raise MCPUnavailable(f"get_positions failed: {exc}") from exc

    def get_quote(self, symbol: str) -> Decimal:
        if self._session is None:
            self.connect()
        try:
            result = self._call_tool("get_equity_quotes", {"symbols": [symbol]})
            row = result["quotes"][0]
            return Decimal(str(row["last_trade_price"]))
        except Exception as exc:
            raise MCPUnavailable(f"get_quote failed: {exc}") from exc

    def place_equity_order(
        self, *, symbol: str, side: Side,
        notional: Decimal | None, quantity: Decimal | None,
        order_type: OrderType, limit_price: Decimal | None,
        client_order_id: str,
    ) -> MCPOrderAck:
        if self._session is None:
            self.connect()
        params: dict = {
            "symbol": symbol,
            "side": side.value.lower(),
            "type": order_type.value.lower(),
            "client_order_id": client_order_id,
        }
        if notional is not None:
            params["notional"] = str(notional)
        if quantity is not None:
            params["quantity"] = str(quantity)
        if limit_price is not None:
            params["limit_price"] = str(limit_price)
        try:
            result = self._call_tool("place_equity_order", params)
            return MCPOrderAck(
                order_id=result["id"], client_order_id=client_order_id,
                symbol=symbol, side=side,
                quantity=Decimal(str(result["quantity"])) if result.get("quantity") is not None else None,
                notional=Decimal(str(result["notional"])) if result.get("notional") is not None else None,
                status=result["status"],
                fill_price=Decimal(str(result["fill_price"])) if result.get("fill_price") is not None else None,
            )
        except Exception as exc:
            raise MCPUnavailable(f"place_equity_order failed: {exc}") from exc

    def cancel_equity_order(self, order_id: str) -> None:
        if self._session is None:
            self.connect()
        try:
            self._call_tool("cancel_equity_order", {"id": order_id})
        except Exception as exc:
            raise MCPUnavailable(f"cancel_equity_order failed: {exc}") from exc
