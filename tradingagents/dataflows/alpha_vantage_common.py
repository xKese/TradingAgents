import json
import os
import re
import threading
import time
from datetime import datetime
from io import StringIO

import pandas as pd
import requests
from dateutil import parser as date_parser

from .errors import VendorNotConfiguredError, VendorRateLimitError

API_BASE_URL = "https://www.alphavantage.co/query"

# Network timeout (seconds) so a stalled Alpha Vantage request can't hang the
# CLI/agents indefinitely (#990).
REQUEST_TIMEOUT = 30

# Alpha Vantage rejects bursts above ~5 requests/second. Agent tool loops fire
# calls back to back, so space requests at least this far apart (≈4 req/s).
MIN_REQUEST_INTERVAL = 0.25

# Transient burst throttles ("Burst pattern detected ... no more than 5
# requests per second") are retried with these backoffs before giving up.
_BURST_BACKOFFS = (1.5, 3.0)

_throttle_lock = threading.Lock()
_last_request_at = 0.0


class AlphaVantageNotConfiguredError(VendorNotConfiguredError):
    """Raised when Alpha Vantage is selected but no API key is configured.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """
    pass


def get_api_key() -> str:
    """Retrieve the API key for Alpha Vantage from environment variables."""
    api_key = os.getenv("ALPHA_VANTAGE_API_KEY")
    if api_key:
        # Strip whitespace and surrounding quotes: docker-compose's env_file does
        # NOT strip quotes, so ALPHA_VANTAGE_API_KEY="key" reaches the process
        # with the quotes attached and Alpha Vantage rejects it. Real keys are
        # alphanumeric with none of these characters, so this is safe.
        api_key = api_key.strip().strip('"').strip("'").strip()
    if not api_key:
        raise AlphaVantageNotConfiguredError(
            "ALPHA_VANTAGE_API_KEY environment variable is not set."
        )
    return api_key

# First embedded ISO date (optionally with a time part) inside a noisy string.
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")


def parse_date(value) -> datetime:
    """Leniently parse an LLM/app-supplied date into a ``datetime``.

    Date arguments to the market/news tools are produced by the LLM and may
    carry a trailing time or whitespace (``"2024-01-15 00:00:00"``,
    ``"2024-01-15T00:00:00"``) or even free text around the date
    (``"2026-04-18 约3 months back)"``). When full-string parsing fails,
    extract the first embedded ISO date instead — deterministic, unlike
    dateutil's ``fuzzy=True``, which can misread loose digits from the noise
    as a day or month. Only a string with no recognizable date at all still
    raises, so garbage fails loudly rather than being silently guessed.
    """
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    try:
        return date_parser.parse(text)
    except (ValueError, OverflowError):
        match = _ISO_DATE_RE.search(text)
        if match:
            return date_parser.parse(match.group(0))
        raise


def format_datetime_for_api(date_input) -> str:
    """Convert a date/datetime to the YYYYMMDDTHHMM format Alpha Vantage expects."""
    # Already in the API's own YYYYMMDDTHHMM shape.
    if isinstance(date_input, str) and len(date_input) == 13 and "T" in date_input:
        return date_input
    return parse_date(date_input).strftime("%Y%m%dT%H%M")

class AlphaVantageRateLimitError(VendorRateLimitError):
    """Raised when the Alpha Vantage API rate limit is exceeded."""
    pass

def _make_api_request(function_name: str, params: dict) -> dict | str:
    """Helper function to make API requests and handle responses.

    Raises:
        AlphaVantageRateLimitError: When API rate limit is exceeded
    """
    # Create a copy of params to avoid modifying the original
    api_params = params.copy()
    api_params.update({
        "function": function_name,
        "apikey": get_api_key(),
        "source": "trading_agents",
    })

    # Handle entitlement parameter if present in params or global variable
    current_entitlement = globals().get('_current_entitlement')
    entitlement = api_params.get("entitlement") or current_entitlement

    if entitlement:
        api_params["entitlement"] = entitlement
    elif "entitlement" in api_params:
        # Remove entitlement if it's None or empty
        api_params.pop("entitlement", None)

    for attempt in range(len(_BURST_BACKOFFS) + 1):
        _throttle()
        response = requests.get(API_BASE_URL, params=api_params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        response_text = response.text

        # Error responses are JSON; data responses are usually CSV (or data-keyed
        # JSON). A non-JSON body is normal data.
        try:
            response_json = json.loads(response_text)
        except json.JSONDecodeError:
            return response_text

        # Alpha Vantage reports problems via "Information" / "Note". Classify so a
        # genuine rate limit and an invalid/missing key aren't conflated (#991):
        # rate-limit phrasing is checked first because those notices also mention
        # "API key" ("your API key ... 25 requests per day").
        notice = response_json.get("Information") or response_json.get("Note")
        if notice:
            low = notice.lower()
            # Burst throttles ("Burst pattern detected ... 5 requests per
            # second") are transient — back off and retry before failing.
            # Checked before the daily-cap markers so a burst never surfaces
            # as an unrecoverable daily limit. Without this branch the notice
            # matched no marker at all and was returned as data, ending up in
            # the CSV parser downstream.
            # Markers must not overlap the legacy per-minute cap notice
            # ("API call frequency is 5 calls per minute"), which is a hard
            # limit handled below.
            if any(m in low for m in ("burst", "per second", "spreading out")):
                if attempt < len(_BURST_BACKOFFS):
                    time.sleep(_BURST_BACKOFFS[attempt])
                    continue
                raise AlphaVantageRateLimitError(
                    f"Alpha Vantage burst throttle persisted: {notice}"
                )
            if any(m in low for m in ("rate limit", "requests per day", "call frequency", "premium")):
                raise AlphaVantageRateLimitError(f"Alpha Vantage rate limit exceeded: {notice}")
            if "api key" in low or "apikey" in low:
                # Reuse the existing "not configured" error so a bad key surfaces as
                # a real, actionable failure rather than a mislabeled rate limit (#991).
                raise AlphaVantageNotConfiguredError(f"Alpha Vantage API key invalid or missing: {notice}")

        return response_text


def _throttle() -> None:
    """Space Alpha Vantage requests at least MIN_REQUEST_INTERVAL apart.

    Agent tool loops issue requests back to back; without pacing they trip
    the vendor's ~5 req/s burst detector. Lock-guarded so concurrent web
    runs share the same budget.
    """
    global _last_request_at
    with _throttle_lock:
        wait = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.monotonic()



def _filter_csv_by_date_range(csv_data: str, start_date: str, end_date: str) -> str:
    """
    Filter CSV data to include only rows within the specified date range.

    Args:
        csv_data: CSV string from Alpha Vantage API
        start_date: Start date in yyyy-mm-dd format
        end_date: End date in yyyy-mm-dd format

    Returns:
        Filtered CSV string
    """
    if not csv_data or csv_data.strip() == "":
        return csv_data

    try:
        # Parse CSV data
        df = pd.read_csv(StringIO(csv_data))

        # Assume the first column is the date column (timestamp)
        date_col = df.columns[0]
        df[date_col] = pd.to_datetime(df[date_col])

        # Filter by date range
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        filtered_df = df[(df[date_col] >= start_dt) & (df[date_col] <= end_dt)]

        # Convert back to CSV string
        return filtered_df.to_csv(index=False)

    except Exception as e:
        # If filtering fails, return original data with a warning
        print(f"Warning: Failed to filter CSV data by date range: {e}")
        return csv_data
