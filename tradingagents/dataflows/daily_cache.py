"""Per-day on-disk cache for vendor data calls.

Opt-in via the ``data_cache_daily`` config flag: when enabled, responses for
slow-moving data (news, macro, fundamentals, insider, prediction markets) are
cached under ``data_cache_dir/daily/<YYYY-MM-DD>/`` so repeated runs on the
same calendar day see identical inputs — one of the levers for reducing
run-to-run rating variation. Price/indicator data is excluded; it has its own
cache with look-ahead and staleness guards (``stockstats_utils.load_ohlcv``).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import date, timedelta

from .config import get_config

logger = logging.getLogger(__name__)

# Methods whose results are stable enough to freeze for a day. Anything not
# listed here always fetches live, even with the cache enabled.
CACHEABLE_METHODS = frozenset({
    "get_news",
    "get_global_news",
    "get_insider_transactions",
    "get_macro_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_prediction_markets",
})

# route_to_vendor degrades failures to these instructive sentinels; caching one
# would freeze a transient outage for the whole day.
_SENTINEL_PREFIXES = ("NO_DATA_AVAILABLE:", "DATA_UNAVAILABLE:")

_RETENTION_DAYS = 7


def _cache_path(cache_root: str, method: str, args: tuple, kwargs: dict) -> str:
    payload = json.dumps(
        [method, [str(a) for a in args], sorted((k, str(v)) for k, v in kwargs.items())],
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return os.path.join(
        cache_root, "daily", date.today().isoformat(), f"{method}__{digest}.md"
    )


def _write_atomic(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _prune_old_days(daily_root: str) -> None:
    """Best-effort removal of day directories past the retention window."""
    cutoff = date.today() - timedelta(days=_RETENTION_DAYS)
    try:
        entries = os.listdir(daily_root)
    except OSError:
        return
    for name in entries:
        try:
            if date.fromisoformat(name) < cutoff:
                shutil.rmtree(os.path.join(daily_root, name), ignore_errors=True)
        except ValueError:
            continue  # not a day directory


def cached_vendor_call(method: str, impl, args: tuple, kwargs: dict):
    """Serve ``impl()`` through the per-day cache when eligible.

    Only string results are cached (vendor tools return prompt-ready text);
    failure sentinels are never persisted, so a transient outage can't poison
    the rest of the day.
    """
    config = get_config()
    cache_root = config.get("data_cache_dir")
    if (
        not config.get("data_cache_daily")
        or method not in CACHEABLE_METHODS
        or not cache_root
    ):
        return impl()

    path = _cache_path(cache_root, method, args, kwargs)
    try:
        with open(path, encoding="utf-8") as fh:
            logger.debug("Daily cache hit for %s (%s)", method, path)
            return fh.read()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("Daily cache unreadable for %s (%s): %s", method, path, exc)

    result = impl()

    if (
        isinstance(result, str)
        and result
        and not result.startswith(_SENTINEL_PREFIXES)
    ):
        try:
            _write_atomic(path, result)
            _prune_old_days(os.path.join(cache_root, "daily"))
        except OSError as exc:
            logger.warning("Could not write daily cache for %s: %s", method, exc)
    return result
