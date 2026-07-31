"""Empty Alpha Vantage fundamentals payloads raise NoMarketDataError.

AV's fundamentals coverage is US-centric: non-US listings (e.g. ADS.DEX for
adidas on XETRA) return `{}` or empty report lists with HTTP 200. The typed
error makes the vendor router fall back to the next vendor (or emit the
NO_DATA sentinel) instead of handing the analyst raw `{}` — and keeps the
daily cache from storing the empty result for the rest of the day.
"""

import copy
import json
import unittest
from unittest import mock

import pytest

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import alpha_vantage_fundamentals as av_fund, interface
from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.errors import NoMarketDataError

VALID_STATEMENT = json.dumps({
    "symbol": "IBM",
    "annualReports": [{"fiscalDateEnding": "2025-12-31", "totalRevenue": "1"}],
    "quarterlyReports": [],
})
EMPTY_REPORTS = json.dumps({
    "symbol": "ADS.DEX", "annualReports": [], "quarterlyReports": [],
})

STATEMENT_FUNCS = (
    av_fund.get_balance_sheet,
    av_fund.get_cashflow,
    av_fund.get_income_statement,
)


def _patched_request(body: str):
    return mock.patch.object(av_fund, "_make_api_request", return_value=body)


@pytest.mark.unit
class EmptyPayloadTests(unittest.TestCase):
    def test_empty_object_raises_for_all_four_functions(self):
        for func in STATEMENT_FUNCS + (av_fund.get_fundamentals,):
            with self.subTest(func=func.__name__), _patched_request("{}"):
                with self.assertRaises(NoMarketDataError) as ctx:
                    func("ADS.DEX")
                self.assertIn("Alpha Vantage", ctx.exception.detail)
                self.assertEqual(ctx.exception.symbol, "ADS.DEX")

    def test_empty_report_lists_raise_for_statements(self):
        for func in STATEMENT_FUNCS:
            with self.subTest(func=func.__name__), _patched_request(EMPTY_REPORTS):
                with self.assertRaises(NoMarketDataError) as ctx:
                    func("ADS.DEX")
                self.assertIn("no annual/quarterly reports", ctx.exception.detail)

    def test_error_message_payload_raises_with_detail(self):
        body = json.dumps({"Error Message": "Invalid API call for ADS.DEX"})
        with _patched_request(body), self.assertRaises(NoMarketDataError) as ctx:
            av_fund.get_income_statement("ADS.DEX")
        self.assertIn("Invalid API call", ctx.exception.detail)

    def test_valid_payload_passes_through(self):
        with _patched_request(VALID_STATEMENT):
            out = av_fund.get_income_statement("IBM")
        self.assertEqual(json.loads(out), json.loads(VALID_STATEMENT))

    def test_overview_with_data_passes_through(self):
        body = json.dumps({"Symbol": "IBM", "MarketCapitalization": "1"})
        with _patched_request(body):
            self.assertEqual(av_fund.get_fundamentals("IBM"), body)

    def test_non_json_body_fails_open(self):
        # e.g. an HTML error page: pass through unchanged, never raise here.
        with _patched_request("<html>gateway error</html>"):
            out = av_fund.get_income_statement("IBM")
        self.assertEqual(out, "<html>gateway error</html>")

    def test_all_reports_filtered_by_lookahead_raises(self):
        # Only future-dated reports left after the look-ahead guard → no
        # usable data for the analyst either.
        body = json.dumps({
            "symbol": "IBM",
            "annualReports": [{"fiscalDateEnding": "2099-12-31"}],
            "quarterlyReports": [],
        })
        with _patched_request(body), self.assertRaises(NoMarketDataError):
            av_fund.get_income_statement("IBM", curr_date="2026-01-01")


@pytest.mark.unit
class RouterFallbackTests(unittest.TestCase):
    def setUp(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def tearDown(self):
        config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)

    def test_av_empty_falls_back_to_yfinance(self):
        set_config({"data_vendors": {"fundamental_data": "alpha_vantage,yfinance"}})
        with _patched_request("{}"), mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_income_statement": {
                "alpha_vantage": av_fund.get_income_statement,
                "yfinance": lambda *a, **k: "YF",
            }},
            clear=False,
        ):
            out = interface.route_to_vendor("get_income_statement", "ADS.DE")
        self.assertEqual(out, "YF")

    def test_sole_av_vendor_returns_no_data_sentinel(self):
        set_config({"data_vendors": {"fundamental_data": "alpha_vantage"}})
        with _patched_request("{}"), mock.patch.dict(
            interface.VENDOR_METHODS,
            {"get_income_statement": {
                "alpha_vantage": av_fund.get_income_statement,
            }},
            clear=False,
        ):
            out = interface.route_to_vendor("get_income_statement", "ADS.DE")
        self.assertTrue(out.startswith("NO_DATA_AVAILABLE:"))
        # The router translated ADS.DE to the AV dialect before the call.
        self.assertIn("ADS.DEX", out)


if __name__ == "__main__":
    unittest.main()
