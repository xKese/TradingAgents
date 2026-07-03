from click.testing import CliRunner

from ops.cli import cli
from ops.journal import Journal


def test_notify_once_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("OPS_PUSHOVER_USER_KEY", raising=False)
    path = str(tmp_path / "j.sqlite")
    j = Journal(path)
    j.record_event("fill", {"symbol": "AAPL", "side": "BUY",
                            "quantity": "0.1", "price": "200", "context": "place"})
    j.close()
    res = CliRunner().invoke(cli, ["notify-once", "--journal", path])
    assert res.exit_code == 0
    assert "dispatched" in res.output.lower()
