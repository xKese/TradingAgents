"""Unit tests for `_AsyncWorker`, the daemon-thread asyncio transport.

Pure asyncio coroutines only — no network, no mcp SDK. These pin down the
concurrency contract: submit() runs a coroutine on the worker's single
long-lived loop and returns its result; a timeout cancels only the future,
not the loop, so the worker stays usable afterward; stop() is a clean,
idempotent shutdown.
"""
import asyncio

import pytest

from ops.broker.mcp_client import MCPUnavailable, RealRobinhoodMCPClient, _AsyncWorker


@pytest.fixture
def worker():
    w = _AsyncWorker()
    w.start()
    yield w
    w.stop()


def test_submit_returns_coroutine_result(worker):
    async def _ok():
        return 42

    assert worker.submit(_ok(), timeout=1.0) == 42


def test_submit_timeout_raises_mcp_unavailable_and_worker_stays_usable(worker):
    async def _slow():
        await asyncio.sleep(5)
        return "should not get here"

    with pytest.raises(MCPUnavailable):
        worker.submit(_slow(), timeout=0.05)

    # The loop must still be alive and serving new work after a timeout.
    async def _ok():
        return "still alive"

    assert worker.submit(_ok(), timeout=1.0) == "still alive"


def test_stop_joins_cleanly_and_subsequent_submit_raises():
    w = _AsyncWorker()
    w.start()
    w.stop()

    async def _ok():
        return 1

    with pytest.raises(MCPUnavailable):
        w.submit(_ok(), timeout=1.0)


def test_stop_is_idempotent_and_safe_when_never_started():
    w = _AsyncWorker()
    w.stop()  # never started — must not raise
    w.stop()  # idempotent

    w2 = _AsyncWorker()
    w2.start()
    w2.stop()
    w2.stop()  # idempotent after start


def test_submit_before_start_raises_mcp_unavailable():
    w = _AsyncWorker()

    async def _ok():
        return 1

    with pytest.raises(MCPUnavailable):
        w.submit(_ok(), timeout=1.0)


def test_real_client_construction_does_no_io():
    c = RealRobinhoodMCPClient()
    assert c._worker is None


def test_connect_timeout_stops_worker_thread():
    """A connect() that never becomes ready within connect_timeout must not
    leak the worker daemon thread — the same never-leak contract close()
    documents (Finding 1, final review). Fakes `_serve` as a coroutine that
    hangs forever (no real network); connect_timeout is tiny so the test
    doesn't wait beyond a fraction of a second."""
    client = RealRobinhoodMCPClient(connect_timeout=0.05)

    async def _hanging_serve():
        await asyncio.sleep(100)

    client._serve = _hanging_serve

    with pytest.raises(MCPUnavailable, match="timed out"):
        client.connect()

    assert client._worker is None
    assert client._session is None

    # Also verify it's actually safe to retry: connect() rebuilds a fresh
    # worker rather than reusing a half-torn-down one.
    client._serve = _hanging_serve
    with pytest.raises(MCPUnavailable, match="timed out"):
        client.connect()
    assert client._worker is None


# --- C2: Thread safety of connect()/close() ---


def test_racing_connect_creates_exactly_one_worker():
    """C2: two threads racing connect() on a client whose _serve is a
    controllable fake → exactly one worker and one serve task, both
    threads return connected.

    Construction notes: the fake _serve awaits its release via an executor
    (a bare threading.Event.wait() inside the coroutine would block the
    worker loop itself), and the assertion counts only worker threads
    CREATED BY THIS TEST (set difference against a pre-test snapshot) — a
    process-global thread count is order-dependent under the full suite,
    where another test's daemon worker may still be winding down."""
    import threading

    client = RealRobinhoodMCPClient(connect_timeout=5.0)

    serve_started = threading.Event()
    release = threading.Event()
    serve_calls = []

    async def _blocking_serve():
        serve_calls.append(1)
        serve_started.set()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, release.wait)
        client._session = "fake_session"
        client._ready.set()

    client._serve = _blocking_serve

    before = {t for t in threading.enumerate() if "mcp-async-worker" in t.name}
    results = []

    def do_connect():
        try:
            client.connect()
            results.append("ok")
        except Exception as e:
            results.append(f"err: {e}")

    t1 = threading.Thread(target=do_connect)
    t2 = threading.Thread(target=do_connect)
    t1.start()
    assert serve_started.wait(timeout=2), "first connect never spawned _serve"
    t2.start()

    release.set()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # Both threads connected; only ONE _serve task was ever spawned.
    assert results == ["ok", "ok"]
    assert serve_calls == [1]
    assert client._worker is not None
    after = {t for t in threading.enumerate() if "mcp-async-worker" in t.name}
    assert len(after - before) == 1, "exactly one NEW worker thread"
    client.close()


def test_connect_racing_close_no_leaked_thread():
    """C2: connect() racing close() → no leaked worker thread.
    Assert thread count returns to baseline."""
    import threading

    # Capture baseline worker thread count
    baseline = len([t for t in threading.enumerate() if "mcp-async-worker" in t.name])

    client = RealRobinhoodMCPClient(connect_timeout=0.05)

    async def _hanging_serve():
        await asyncio.sleep(100)

    client._serve = _hanging_serve

    close_done = threading.Event()

    def do_close():
        close_done.wait(timeout=0.1)
        client.close()

    t_close = threading.Thread(target=do_close)
    t_close.start()

    try:
        client.connect()
    except MCPUnavailable:
        pass  # expected: timeout

    # Allow close to complete
    close_done.set()
    t_close.join(timeout=5)

    # No mcp worker threads should remain beyond baseline
    worker_threads = [
        t for t in threading.enumerate() if "mcp-async-worker" in t.name
    ]
    assert len(worker_threads) <= baseline, f"Leaked {len(worker_threads) - baseline} worker threads"
    assert client._worker is None


def test_reconnect_after_transport_death_works_under_lock():
    """C2: reconnect after simulated transport death still works under the lock.

    First connect fails (simulating transport death). Second connect succeeds.
    The lock ensures no double-connect race during the window between the
    first failure and the retry.
    """

    client = RealRobinhoodMCPClient(connect_timeout=5.0)

    # Shared state: track whether we've had a failure
    state = {"failed": True}

    async def _fail_then_succeed():
        if state["failed"]:
            state["failed"] = False
            client._connect_error = MCPUnavailable("transport death")
            client._ready.set()
            raise MCPUnavailable("transport death")
        else:
            client._session = "fake_session"
            client._ready.set()

    client._serve = _fail_then_succeed

    # First connect fails (simulating transport death)
    with pytest.raises(MCPUnavailable, match="transport death"):
        client.connect()

    # _worker survives the failure; _session is None so a retry is possible
    assert client._worker is not None
    first_worker = client._worker

    # Second connect: _serve now succeeds (state["failed"] is False)
    client.connect()

    assert client._session == "fake_session"
    # The worker should still be the same (not rebuilt on reconnection)
    assert client._worker is first_worker
    client.close()


def test_await_fill_window_stays_inside_guardian_cycle():
    """M6 (documented decision): GuardedBroker.place_order holds its lock
    through the fill poll, so the poll window must stay well inside the
    guardian's 60s cycle — a longer window starves stop-loss enforcement
    while an order settles. Anyone changing this default must revisit the
    M6 lock-scope decision recorded in the MCP live design doc."""
    import inspect

    sig = inspect.signature(RealRobinhoodMCPClient._await_fill)
    assert sig.parameters["window_s"].default <= 30.0
