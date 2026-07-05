from datetime import datetime, timedelta, timezone

from ops.journal import Journal
from ops.notify.dispatcher import NotifyDispatcher
from ops.notify.transport import NotifyMessage


class FakeTransport:
    enabled = True
    def __init__(self, fail=False, fail_times=None):
        self.sent = []
        self._fail = fail
        # When set, an int count of leading calls that raise before
        # succeeding (fail-once-then-succeed pattern).
        self._fail_times = fail_times
        self._calls = 0
    def send(self, message: NotifyMessage) -> None:
        self._calls += 1
        if self._fail or (self._fail_times is not None and self._calls <= self._fail_times):
            raise RuntimeError("transport down: password=hunter2 host=smtp.internal.example.com")
        self.sent.append(message)


def _clock(t):
    return lambda: t


def test_routes_by_policy_and_advances_cursor(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("fill", {"symbol": "AAPL", "side": "BUY",
                            "quantity": "0.1", "price": "200", "context": "place"})
    j.record_event("order_rejected", {"symbol": "AAPL"})  # not notified
    push, email = FakeTransport(), FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email})
    sent = d.dispatch_once()
    assert sent == 1                       # only the fill, push channel
    assert len(push.sent) == 1 and len(email.sent) == 0
    assert j.get_cursor("notify") == 2     # advanced past both events
    assert d.dispatch_once() == 0          # nothing new


def test_failure_holds_cursor_and_journals_error(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("kill_switch", {"reason": "weekly -15%"})
    push = FakeTransport(fail=True)
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email})
    d.dispatch_once()
    assert j.get_cursor("notify") == 0     # NOT advanced past the failed event
    errs = [e for e in j.read_events() if e["kind"] == "notify_dispatch_error"]
    assert len(errs) == 1


def test_dispatch_error_payload_is_sanitized(tmp_path):
    """The notify_dispatch_error payload must never leak transport exception
    text (which can contain credentials/hostnames from smtplib/requests).
    Only the exception TYPE name, plus event id/kind, may be journaled."""
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("kill_switch", {"reason": "weekly -15%"})
    push = FakeTransport(fail=True)
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email})
    d.dispatch_once()
    errs = [e for e in j.read_events() if e["kind"] == "notify_dispatch_error"]
    assert len(errs) == 1
    payload = errs[0]["payload"]
    assert payload == {"event_id": 1, "kind": "kill_switch", "error_type": "RuntimeError"}
    serialized = str(payload)
    assert "hunter2" not in serialized
    assert "smtp.internal.example.com" not in serialized
    assert "transport down" not in serialized


def test_cooldown_suppresses_repeat_but_advances(tmp_path):
    j = Journal(str(tmp_path / "j.sqlite"))
    t0 = datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc)
    j.record_event("broker_unreachable", {"err": "timeout"})
    j.record_event("broker_unreachable", {"err": "timeout"})
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": FakeTransport(), "email": email}, now=_clock(t0))
    d.dispatch_once()
    assert len(email.sent) == 1            # second suppressed by cooldown
    assert j.get_cursor("notify") == 2     # both events consumed


def test_failed_throttled_send_does_not_arm_cooldown_and_is_retried(tmp_path):
    """A throttled-kind event whose send fails must NOT arm the cooldown —
    otherwise the retry on the next dispatch_once (still within the cooldown
    window the failed attempt would have armed) is wrongly suppressed and
    the cursor advances past it, dropping the alert (at-least-once
    violation). The cooldown must only arm after a send that completes
    without raising."""
    j = Journal(str(tmp_path / "j.sqlite"))
    t0 = datetime(2026, 7, 2, 15, 0, tzinfo=timezone.utc)
    j.record_event("broker_unreachable", {"err": "timeout"})
    email = FakeTransport(fail_times=1)  # fails on 1st send, succeeds on 2nd
    d = NotifyDispatcher(j, {"push": FakeTransport(), "email": email},
                         now=_clock(t0))

    d.dispatch_once()                      # send raises; cursor held
    assert j.get_cursor("notify") == 0
    assert len(email.sent) == 0

    d.dispatch_once()                      # retry, still "now" == t0
    # The failed first attempt also journaled a notify_dispatch_error event
    # (id 2), consumed alongside the retried broker_unreachable (id 1) on
    # this second call, so the cursor advances past both.
    assert j.get_cursor("notify") == 2
    assert len(email.sent) == 1            # delivered exactly once overall


# --- M1: Poison-pill protection ---


def test_transient_transport_failure_delivers_exactly_once(tmp_path):
    """M1: transient transport failure (fails twice, then succeeds) still
    delivers exactly per at-least-once."""
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("kill_switch", {"reason": "test"})
    push = FakeTransport(fail_times=2)  # fails twice, succeeds on 3rd
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email})

    # First dispatch: fails, cursor held
    d.dispatch_once()
    assert j.get_cursor("notify") == 0
    assert len(push.sent) == 0

    # Second dispatch: still failing
    d.dispatch_once()
    assert j.get_cursor("notify") == 0
    assert len(push.sent) == 0

    # Third dispatch: succeeds, cursor advances past event 1 and any
    # side-effect events (notify_dispatch_error rows).
    d.dispatch_once()
    assert j.get_cursor("notify") > 1  # past kill_switch + error events
    assert len(push.sent) == 1          # delivered exactly once


# --- M2: First-enable cursor fast-forward (age-based) ---


def _record_event_at(j, monkeypatch, at, kind, payload):
    """Backdate a journal event: record_event stamps wall-clock time, so
    pin the module's _now_iso for one call (same pattern as test_summary)."""
    import ops.journal as journal_mod
    monkeypatch.setattr(journal_mod, "_now_iso", lambda: at.isoformat())
    j.record_event(kind, payload)
    monkeypatch.undo()


def test_fresh_consumer_skips_stale_backlog(tmp_path, monkeypatch):
    """M2: fresh consumer + a journal of OLD events → 0 sends, cursor
    fast-forwarded past them, one notify_cursor_initialized. Age is the
    discriminator, not event count — a 3-event stale backlog is still a
    storm nobody asked for."""
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=2)
    for _ in range(3):
        _record_event_at(j, monkeypatch, old, "stop_hit",
                         {"symbol": "AAPL", "pct": "-0.09"})
    push = FakeTransport()
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email}, now=_clock(now))

    sent = d.dispatch_once()
    assert sent == 0
    assert push.sent == [] and email.sent == []
    init_events = [e for e in j.read_events() if e["kind"] == "notify_cursor_initialized"]
    assert len(init_events) == 1
    assert init_events[0]["payload"]["skipped_through"] == 3


def test_fresh_consumer_delivers_recent_startup_events(tmp_path):
    """M2: a fresh consumer must NOT skip recent events. The first-ever
    notify-enabled startup journals critical events (startup_halted,
    inconsistency) moments before the first dispatch tick — swallowing
    those in a fast-forward would lose the exact alert the user enabled
    notifications for."""
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("startup_halted", {"reason": "reconciliation"})
    push = FakeTransport()
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email})

    sent = d.dispatch_once()
    assert sent == 2  # startup_halted is push + email
    init_events = [e for e in j.read_events() if e["kind"] == "notify_cursor_initialized"]
    assert init_events == []


def test_fresh_consumer_mixed_backlog_delivers_only_recent(tmp_path, monkeypatch):
    """M2: stale events are skipped and recent ones delivered — in the same
    first dispatch."""
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    _record_event_at(j, monkeypatch, now - timedelta(days=2), "stop_hit",
                     {"symbol": "AAPL", "pct": "-0.09"})
    _record_event_at(j, monkeypatch, now - timedelta(minutes=5), "kill_switch",
                     {"pct": "-0.2", "threshold": "-0.15",
                      "equity_now": "200", "equity_open_week": "250", "mode": "paper"})
    push = FakeTransport()
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email}, now=_clock(now))

    sent = d.dispatch_once()
    assert sent == 2  # only the kill_switch; the stale stop_hit is skipped
    assert [m.title for m in push.sent] == ["KILL SWITCH TRIPPED"]
    init_events = [e for e in j.read_events() if e["kind"] == "notify_cursor_initialized"]
    assert len(init_events) == 1
    assert init_events[0]["payload"]["skipped_through"] == 1


def test_new_event_after_init_is_delivered(tmp_path, monkeypatch):
    """M2: a new event after initialization is delivered normally."""
    j = Journal(str(tmp_path / "j.sqlite"))
    now = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=2)
    for _ in range(60):
        _record_event_at(j, monkeypatch, old, "fill",
                         {"symbol": "AAPL", "side": "BUY", "quantity": "0.1",
                          "price": "200", "context": "place"})
    push = FakeTransport()
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email}, now=_clock(now))

    # First dispatch: fast-forward past the stale backlog.
    d.dispatch_once()
    assert j.get_cursor("notify") >= 60

    # Add a new event
    j.record_event("kill_switch", {"reason": "new"})

    # Second dispatch: delivers the new event
    sent = d.dispatch_once()
    assert sent == 2  # kill_switch → push + email
    assert j.get_cursor("notify") == 62  # 60 old + 1 cursor_init + 1 kill_switch


def test_existing_cursor_is_not_fast_forwarded(tmp_path):
    """M2: an existing cursor (including a legitimate 0-with-row) is never
    fast-forwarded."""
    j = Journal(str(tmp_path / "j.sqlite"))
    for _ in range(20):
        j.record_event("fill", {"symbol": "AAPL", "side": "BUY",
                                "quantity": "0.1", "price": "200", "context": "place"})

    # Set an existing cursor at 0 (simulating a legitimate 0-with-row)
    j.set_cursor("notify", 0)

    push = FakeTransport()
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email})

    # Dispatch should NOT fast-forward (cursor row exists)
    d.dispatch_once()
    # Cursor should have advanced past all 20 events
    assert j.get_cursor("notify") == 20
    # No notify_cursor_initialized event
    init_events = [e for e in j.read_events() if e["kind"] == "notify_cursor_initialized"]
    assert len(init_events) == 0


def test_render_error_advances_cursor(tmp_path):
    """M1: an event whose render raises → cursor advances, later events
    still delivered, notify_render_error journaled once (not per tick)."""
    j = Journal(str(tmp_path / "j.sqlite"))
    from ops.notify import dispatcher as disp_mod
    original_render = disp_mod.render

    def _broken_render(kind, payload):
        if kind == "fill" and payload.get("bad_key"):
            raise KeyError("missing_key")
        return original_render(kind, payload)

    disp_mod.render = _broken_render
    try:
        # kill_switch is in policy (push+email) but has no render override,
        # so the broken render won't affect it. Fill is in policy (push) and
        # has a broken payload.
        j.record_event("kill_switch", {"reason": "weekly -15%"})
        j.record_event("fill", {"symbol": "AAPL", "side": "BUY",
                                "quantity": "0.1", "price": "200",
                                "context": "place", "bad_key": True})

        push = FakeTransport()
        email = FakeTransport()
        d = NotifyDispatcher(j, {"push": push, "email": email})
        sent = d.dispatch_once()

        # Cursor should have advanced past BOTH events
        assert j.get_cursor("notify") == 2
        # Only the kill_switch should have been sent (push+email)
        assert sent == 2
        assert len(push.sent) == 1
        assert len(email.sent) == 1
        # Render error should be journaled once
        errs = [e for e in j.read_events() if e["kind"] == "notify_render_error"]
        assert len(errs) == 1
        assert errs[0]["payload"]["error_type"] == "KeyError"
    finally:
        disp_mod.render = original_render


def test_transport_failures_bounded(tmp_path):
    """M1: transport failing 10 times → event skipped with notify_event_skipped,
    subsequent events delivered."""
    j = Journal(str(tmp_path / "j.sqlite"))
    j.record_event("kill_switch", {"reason": "test"})
    j.record_event("fill", {"symbol": "AAPL", "side": "BUY",
                            "quantity": "0.1", "price": "200", "context": "place"})
    # Transport fails only on the kill_switch, succeeds for everything else
    push = FakeTransport(fail_times=10)  # fails 10 times, then succeeds
    email = FakeTransport()
    d = NotifyDispatcher(j, {"push": push, "email": email})

    # Dispatch 9 times — cursor held at 0
    for _ in range(9):
        d.dispatch_once()
    assert j.get_cursor("notify") == 0

    # 10th dispatch — skip (fail count reaches 10)
    d.dispatch_once()
    assert j.get_cursor("notify") == 1

    # Check that notify_event_skipped was journaled
    skipped = [e for e in j.read_events() if e["kind"] == "notify_event_skipped"]
    assert len(skipped) == 1
    assert skipped[0]["payload"]["consecutive_failures"] == 10

    # Next dispatch: kill_switch error events are consumed (no policy),
    # then the fill event is delivered (transport now succeeds)
    d.dispatch_once()
    assert j.get_cursor("notify") > 1  # past fill + error events
    assert len(push.sent) == 1         # fill delivered
