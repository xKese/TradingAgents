"""research_vetting_run / research_vetting_error event contracts."""
from ops import events


def test_vetting_kinds_are_registered_and_audit_only():
    for kind in (events.KIND_RESEARCH_VETTING_RUN,
                 events.KIND_RESEARCH_VETTING_ERROR):
        assert kind in events.BUILDERS
        assert kind in events.AUDIT_ONLY


def test_vetting_run_payload_shape():
    payload = events.research_vetting_run_payload(
        asof="2026-07-09", vetted=3, confirmed=1, rejected=1, failed=1,
        still_pending=2, hit_deadline=True,
    )
    assert payload == {
        "asof": "2026-07-09", "vetted": 3, "confirmed": 1, "rejected": 1,
        "failed": 1, "still_pending": 2, "hit_deadline": True,
    }


def test_vetting_error_payload_shape():
    assert events.research_vetting_error_payload(error="Boom: x") == {
        "error": "Boom: x",
    }
