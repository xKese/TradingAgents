import threading
import time

from .alpha_vantage_common import (
    _make_api_request,
    parse_date,
)
from .errors import VendorError

# Short-lived response cache so indicator variants served by one endpoint
# (boll/boll_ub/boll_lb -> BBANDS, macd/macds/macdh -> MACD) reuse a single
# HTTP request instead of firing three identical ones back to back — the
# exact pattern that trips Alpha Vantage's burst detector. 5 minutes covers
# one analyst run (including LLM latency between tool calls) while staying
# irrelevant for daily-interval data. Successful responses only; errors are
# never cached. Per-process, capped, keyed on the full request params.
_RESPONSE_CACHE_TTL = 300.0
_RESPONSE_CACHE_MAX_ENTRIES = 64
_response_cache: dict[tuple, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _fetch_indicator_data(function_name: str, params: dict) -> str:
    """Fetch one Alpha Vantage indicator payload, deduplicated via TTL cache."""
    key = (function_name, tuple(sorted(params.items())))
    now = time.monotonic()
    with _cache_lock:
        cached = _response_cache.get(key)
        if cached is not None:
            stored_at, payload = cached
            if now - stored_at < _RESPONSE_CACHE_TTL:
                return payload
            del _response_cache[key]

    # Fetch outside the lock: two threads racing on the same key may both
    # fetch (rare, harmless); the sequential per-run tool loop dedups exactly.
    data = _make_api_request(function_name, params)

    if isinstance(data, str):
        with _cache_lock:
            if len(_response_cache) >= _RESPONSE_CACHE_MAX_ENTRIES:
                oldest = min(_response_cache, key=lambda k: _response_cache[k][0])
                del _response_cache[oldest]
            _response_cache[key] = (time.monotonic(), data)
    return data


def get_indicator(
    symbol: str,
    indicator: str,
    curr_date: str,
    look_back_days: int,
    interval: str = "daily",
    time_period: int = 14,
    series_type: str = "close"
) -> str:
    """
    Returns Alpha Vantage technical indicator values over a time window.

    Args:
        symbol: ticker symbol of the company
        indicator: technical indicator to get the analysis and report of
        curr_date: The current trading date you are trading on, YYYY-mm-dd
        look_back_days: how many days to look back
        interval: Time interval (daily, weekly, monthly)
        time_period: Number of data points for calculation
        series_type: The desired price type (close, open, high, low)

    Returns:
        String containing indicator values and description
    """
    from datetime import datetime

    from dateutil.relativedelta import relativedelta

    supported_indicators = {
        "close_50_sma": ("50 SMA", "close"),
        "close_200_sma": ("200 SMA", "close"),
        "close_10_ema": ("10 EMA", "close"),
        "macd": ("MACD", "close"),
        "macds": ("MACD Signal", "close"),
        "macdh": ("MACD Histogram", "close"),
        "rsi": ("RSI", "close"),
        "boll": ("Bollinger Middle", "close"),
        "boll_ub": ("Bollinger Upper Band", "close"),
        "boll_lb": ("Bollinger Lower Band", "close"),
        "atr": ("ATR", None),
        "vwma": ("VWMA", "close")
    }

    indicator_descriptions = {
        "close_50_sma": "50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.",
        "close_200_sma": "200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.",
        "close_10_ema": "10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.",
        "macd": "MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.",
        "macds": "MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.",
        "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.",
        "rsi": "RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.",
        "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.",
        "boll_ub": "Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.",
        "boll_lb": "Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.",
        "atr": "ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.",
        "vwma": "VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    }

    if indicator not in supported_indicators:
        raise ValueError(
            f"Indicator {indicator} is not supported. Please choose from: {list(supported_indicators.keys())}"
        )

    curr_date_dt = parse_date(curr_date)
    before = curr_date_dt - relativedelta(days=look_back_days)

    # Get the full data for the period instead of making individual calls
    _, required_series_type = supported_indicators[indicator]

    # Use the provided series_type or fall back to the required one
    if required_series_type:
        series_type = required_series_type

    if indicator == "vwma":
        # Alpha Vantage doesn't have direct VWMA, so we'll return an informative message
        # In a real implementation, this would need to be calculated from OHLCV data
        return f"## VWMA (Volume Weighted Moving Average) for {symbol}:\n\nVWMA calculation requires OHLCV data and is not directly available from Alpha Vantage API.\nThis indicator would need to be calculated from the raw stock data using volume-weighted price averaging.\n\n{indicator_descriptions.get('vwma', 'No description available.')}"

    # Resolve each indicator to its (endpoint, params) request spec first, so
    # variants of one endpoint (all three Bollinger bands, all three MACD
    # series) build identical specs and collapse onto a single cached request.
    if indicator == "close_50_sma":
        spec = ("SMA", {
            "symbol": symbol,
            "interval": interval,
            "time_period": "50",
            "series_type": series_type,
            "datatype": "csv"
        })
    elif indicator == "close_200_sma":
        spec = ("SMA", {
            "symbol": symbol,
            "interval": interval,
            "time_period": "200",
            "series_type": series_type,
            "datatype": "csv"
        })
    elif indicator == "close_10_ema":
        spec = ("EMA", {
            "symbol": symbol,
            "interval": interval,
            "time_period": "10",
            "series_type": series_type,
            "datatype": "csv"
        })
    elif indicator in ("macd", "macds", "macdh"):
        spec = ("MACD", {
            "symbol": symbol,
            "interval": interval,
            "series_type": series_type,
            "datatype": "csv"
        })
    elif indicator == "rsi":
        spec = ("RSI", {
            "symbol": symbol,
            "interval": interval,
            "time_period": str(time_period),
            "series_type": series_type,
            "datatype": "csv"
        })
    elif indicator in ("boll", "boll_ub", "boll_lb"):
        spec = ("BBANDS", {
            "symbol": symbol,
            "interval": interval,
            "time_period": "20",
            "series_type": series_type,
            "datatype": "csv"
        })
    elif indicator == "atr":
        spec = ("ATR", {
            "symbol": symbol,
            "interval": interval,
            "time_period": str(time_period),
            "datatype": "csv"
        })
    else:
        return f"Error: Indicator {indicator} not implemented yet."

    try:
        data = _fetch_indicator_data(*spec)

        # Parse CSV data and extract values for the date range
        lines = data.strip().split('\n')
        if len(lines) < 2:
            return f"Error: No data returned for {indicator}"

        # Parse header and data
        header = [col.strip() for col in lines[0].split(',')]
        try:
            date_col_idx = header.index('time')
        except ValueError:
            return f"Error: 'time' column not found in data for {indicator}. Available columns: {header}"

        # Map internal indicator names to expected CSV column names from Alpha Vantage
        col_name_map = {
            "macd": "MACD", "macds": "MACD_Signal", "macdh": "MACD_Hist",
            "boll": "Real Middle Band", "boll_ub": "Real Upper Band", "boll_lb": "Real Lower Band",
            "rsi": "RSI", "atr": "ATR", "close_10_ema": "EMA",
            "close_50_sma": "SMA", "close_200_sma": "SMA"
        }

        target_col_name = col_name_map.get(indicator)

        if not target_col_name:
            # Default to the second column if no specific mapping exists
            value_col_idx = 1
        else:
            try:
                value_col_idx = header.index(target_col_name)
            except ValueError:
                return f"Error: Column '{target_col_name}' not found for indicator '{indicator}'. Available columns: {header}"

        result_data = []
        for line in lines[1:]:
            if not line.strip():
                continue
            values = line.split(',')
            if len(values) > value_col_idx:
                try:
                    date_str = values[date_col_idx].strip()
                    # Parse the date
                    date_dt = datetime.strptime(date_str, "%Y-%m-%d")

                    # Check if date is in our range
                    if before <= date_dt <= curr_date_dt:
                        value = values[value_col_idx].strip()
                        result_data.append((date_dt, value))
                except (ValueError, IndexError):
                    continue

        # Sort by date and format output
        result_data.sort(key=lambda x: x[0])

        ind_string = ""
        for date_dt, value in result_data:
            ind_string += f"{date_dt.strftime('%Y-%m-%d')}: {value}\n"

        if not ind_string:
            ind_string = "No data available for the specified date range.\n"

        result_str = (
            f"## {indicator.upper()} values from {before.strftime('%Y-%m-%d')} to {curr_date_dt.strftime('%Y-%m-%d')}:\n\n"
            + ind_string
            + "\n\n"
            + indicator_descriptions.get(indicator, "No description available.")
        )

        return result_str

    except VendorError:
        # Vendor-level failures (missing API key, rate limit, no data) must
        # propagate so the router can fall back to the next configured vendor
        # instead of the LLM receiving this as a successful-looking error
        # string. Notably AlphaVantageRateLimitError: swallowing it here meant
        # a throttled Alpha Vantage never fell back to yfinance.
        raise
    except Exception as e:
        print(f"Error getting Alpha Vantage indicator data for {indicator}: {e}")
        return f"Error retrieving {indicator} data: {str(e)}"
