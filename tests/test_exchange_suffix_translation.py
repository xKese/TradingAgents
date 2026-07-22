"""Exchange-suffix dialect translation between the canonical (Yahoo) symbol
the pipeline holds and Alpha Vantage's native suffixes at vendor dispatch.

The router translates MBG.F -> MBG.FRK only for alpha_vantage calls on
ticker-first methods, keeping yfinance calls and daily-cache keys canonical —
so a yfinance->alpha_vantage fallback chain works for exchange-suffixed
symbols regardless of which dialect the user typed.
"""

import copy
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.symbol_utils import NoMarketDataError


def _reset_config():
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


class _Capture:
    def __init__(self, result="DATA"):
        self.symbols = []
        self.result = result

    def __call__(self, symbol, *a, **k):
        self.symbols.append(symbol)
        return self.result


def _no_data(symbol, *a, **k):
    raise NoMarketDataError(symbol, symbol, "no rows")


@pytest.mark.unit
class SuffixTranslationRoutingTests(unittest.TestCase):
    def setUp(self):
        _reset_config()

    def tearDown(self):
        _reset_config()

    def _patch(self, method, vendors):
        return mock.patch.dict(interface.VENDOR_METHODS, {method: vendors}, clear=False)

    def test_alpha_vantage_receives_av_dialect(self):
        set_config({"data_vendors": {"fundamental_data": "alpha_vantage"}})
        av = _Capture()
        with self._patch("get_fundamentals", {"alpha_vantage": av}):
            result = interface.route_to_vendor("get_fundamentals", "ADS.DE", "2026-01-01")
        self.assertEqual(result, "DATA")
        self.assertEqual(av.symbols, ["ADS.DEX"])

    def test_yfinance_receives_canonical_symbol(self):
        set_config({"data_vendors": {"fundamental_data": "yfinance"}})
        yf = _Capture()
        with self._patch("get_fundamentals", {"yfinance": yf}):
            interface.route_to_vendor("get_fundamentals", "ADS.DE", "2026-01-01")
        self.assertEqual(yf.symbols, ["ADS.DE"])

    def test_fallback_chain_translates_per_vendor(self):
        # The headline scenario: yfinance misses, Alpha Vantage serves — and
        # each vendor sees its own dialect of the same instrument.
        set_config({"data_vendors": {"fundamental_data": "yfinance,alpha_vantage"}})
        av = _Capture(result="AV DATA")
        with self._patch("get_fundamentals", {"yfinance": _no_data, "alpha_vantage": av}):
            result = interface.route_to_vendor("get_fundamentals", "MBG.F", "2026-01-01")
        self.assertEqual(result, "AV DATA")
        self.assertEqual(av.symbols, ["MBG.FRK"])

    def test_unknown_suffix_forwarded_verbatim_to_av(self):
        set_config({"data_vendors": {"fundamental_data": "alpha_vantage"}})
        av = _Capture()
        with self._patch("get_fundamentals", {"alpha_vantage": av}):
            interface.route_to_vendor("get_fundamentals", "0700.HK", "2026-01-01")
        self.assertEqual(av.symbols, ["0700.HK"])

    def test_non_ticker_method_untouched(self):
        # get_global_news takes a date first — must never be "translated".
        set_config({"data_vendors": {"news_data": "alpha_vantage"}})
        seen = []

        def av_global(curr_date, *a, **k):
            seen.append(curr_date)
            return "NEWS"

        with self._patch("get_global_news", {"alpha_vantage": av_global}):
            interface.route_to_vendor("get_global_news", "2026-01-15", 7, 10)
        self.assertEqual(seen, ["2026-01-15"])

    def test_daily_cache_key_stays_canonical(self):
        # Translation happens below the cache: the canonical symbol keys the
        # cache entry, and a hit never re-invokes the vendor.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            set_config({
                "data_vendors": {"news_data": "alpha_vantage"},
                "data_cache_daily": True,
                "data_cache_dir": tmp,
            })
            av = _Capture(result="CACHED NEWS")
            with self._patch("get_news", {"alpha_vantage": av}):
                first = interface.route_to_vendor("get_news", "ADS.DE", "2026-01-01", "2026-01-15")
                second = interface.route_to_vendor("get_news", "ADS.DE", "2026-01-01", "2026-01-15")
        self.assertEqual(first, second)
        self.assertEqual(av.symbols, ["ADS.DEX"])  # exactly one live fetch
