"""One-off: normalize OPEN memos' drawdown falsifiers to canonical form.

Canonical (ops/research/metrics.py): drawdown_from_cost_pct is a POSITIVE
percent below cost, operator > or >=. The pre-convention corpus mixed three
forms; only OPEN memos are ever monitored, so only they are migrated:

  ratio form    (>  0.25)  ->  > 25.0     (threshold in (0, 1) scaled x100)
  signed form   (<= -25.0) ->  >= 25.0    (operator mirrored, sign flipped)

Anything else is left alone and reported. Backs up the DB file next to
itself before writing. Usage:

  python scripts/migrate_drawdown_falsifiers.py [--apply]

Without --apply it only prints what would change.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

from tradingagents.memos.store import default_memo_store_path

_MIRROR = {"<": ">", "<=": ">="}


def _normalize(op: str, thr: float) -> tuple[str, float] | None:
    """Canonical (op, threshold) or None when no rewrite applies."""
    if op in (">", ">=") and 0 < thr < 1:
        return op, thr * 100.0
    if op in _MIRROR and thr < 0:
        return _MIRROR[op], -thr
    return None


def main() -> int:
    apply = "--apply" in sys.argv
    db = default_memo_store_path()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT memo_id, ticker, payload FROM memos WHERE status = 'open'"
    ).fetchall()

    changes: list[tuple[str, str, str]] = []  # (memo_id, ticker, description)
    updates: list[tuple[str, str]] = []       # (payload, memo_id)
    for r in rows:
        payload = json.loads(r["payload"])
        dirty = False
        for f in payload.get("falsifiers", []):
            if f.get("metric") != "drawdown_from_cost_pct":
                continue
            op, thr = f.get("operator"), f.get("threshold")
            if op is None or thr is None:
                continue
            fixed = _normalize(op, float(thr))
            if fixed is None:
                continue
            new_op, new_thr = fixed
            changes.append((
                r["memo_id"], r["ticker"],
                f"{op} {thr}  ->  {new_op} {new_thr}",
            ))
            f["operator"], f["threshold"] = new_op, new_thr
            dirty = True
        if dirty:
            updates.append((json.dumps(payload), r["memo_id"]))

    for memo_id, ticker, desc in changes:
        print(f"{ticker} ({memo_id[:8]}): {desc}")
    if not changes:
        print("nothing to migrate")
        return 0
    if not apply:
        print(f"\n{len(updates)} memo(s) would change — rerun with --apply")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{db}.pre-drawdown-migration.{stamp}"
    shutil.copyfile(db, backup)
    print(f"backup: {backup}")
    with conn:
        conn.executemany(
            "UPDATE memos SET payload = ? WHERE memo_id = ?", updates)
    print(f"migrated {len(updates)} memo(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
