"""yfinance fundamentals/insider failures propagate instead of returning prose.

Previously these functions caught every exception and returned
"Error retrieving ...: <msg>" as a string. The vendor router treats any
string return as success, so a yfinance network error (common on shared
cloud IPs) produced no fallback to the next vendor, no NO_DATA sentinel,
and a day-long cached error string. Raising restores all three behaviors.
"""

import copy
import unittest
from unittest import mock

import pandas as pd
import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import interface, y_finance as yfin
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError

FUNCS = (
    yfin.get_fundamentals,
    yfin.get_balance_sheet,
    yfin.get_cashflow,
    yfin.get_income_statement,
    yfin.get_insider_transactions,
)

STATEMENT_FUNCS = (
    yfin.get_balance_sheet,
    yfin.get_cashflow,
    yfin.get_income_statement,
)


def _patched_retry(side_effect):
    """Patch yf_retry, the choke point every function fetches data through."""
    return mock.patch.object(yfin, "yf_retry", side_effect=side_effect)


@pytest.mark.unit
class ErrorPropagationTests(unittest.TestCase):
    def test_network_errors_propagate_not_stringified(self):
        for func in FUNCS:
            with self.subTest(func=func.__name__), _patched_retry(
                ConnectionError("host unreachable")
            ), self.assertRaises(ConnectionError):
                func("ADS.DE")

    def test_unexpected_errors_propagate(self):
        for func in FUNCS:
            with self.subTest(func=func.__name__), _patched_retry(
                RuntimeError("boom")
            ), self.assertRaises(RuntimeError):
                func("ADS.DE")

    def test_empty_frames_still_raise_no_market_data(self):
        # Regression guard: the typed no-data path is unchanged.
        for func in STATEMENT_FUNCS:
            with self.subTest(func=func.__name__), _patched_retry(
                lambda fn: pd.DataFrame()
            ), self.assertRaises(NoMarketDataError):
                func("ADS.DE")

    def test_empty_info_still_raises_no_market_data(self):
        with _patched_retry(lambda fn: {}), self.assertRaises(NoMarketDataError):
            yfin.get_fundamentals("ADS.DE")

    def test_empty_insider_transactions_is_a_plain_message(self):
        # Empty is normal for insider filings — stays a non-error string.
        with _patched_retry(lambda fn: pd.DataFrame()):
            out = yfin.get_insider_transactions("ADS.DE")
        self.assertIn("No insider transactions reported", out)


@pytest.mark.unit
class RouterFallbackTests(unittest.TestCase):
    def setUp(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def tearDown(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def test_yfinance_error_falls_back_to_alpha_vantage(self):
        set_config({"data_vendors": {"fundamental_data": "yfinance,alpha_vantage"}})
        with _patched_retry(RuntimeError("rate limited")), mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_income_statement": {
                "yfinance": yfin.get_income_statement,
                "alpha_vantage": lambda *a, **k: "AV",
            }},
            clear=False,
        ):
            out = interface.route_to_vendor("get_income_statement", "ADS.DE")
        self.assertEqual(out, "AV")


if __name__ == "__main__":
    unittest.main()
