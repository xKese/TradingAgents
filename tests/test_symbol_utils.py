"""Tests for symbol normalization and the no-data routing sentinel."""

import unittest

import pytest

from tradingagents.dataflows.symbol_utils import (
    NoMarketDataError,
    av_symbol_to_yahoo,
    crypto_base,
    is_yahoo_safe,
    normalize_symbol,
    yahoo_symbol_to_av,
)


@pytest.mark.unit
class TestNormalizeSymbol(unittest.TestCase):
    def test_plain_equities_unchanged(self):
        for sym in ("AAPL", "MSFT", "TSM", "BRK.B", "0700.HK", "^GSPC", "GC=F"):
            self.assertEqual(normalize_symbol(sym), sym)

    def test_lowercases_are_upper(self):
        self.assertEqual(normalize_symbol("aapl"), "AAPL")
        self.assertEqual(normalize_symbol("  msft  "), "MSFT")

    def test_metal_aliases_map_to_futures(self):
        self.assertEqual(normalize_symbol("XAUUSD"), "GC=F")
        self.assertEqual(normalize_symbol("XAUUSD+"), "GC=F")   # broker CFD suffix
        self.assertEqual(normalize_symbol("xauusd+"), "GC=F")
        self.assertEqual(normalize_symbol("GOLD"), "GC=F")
        self.assertEqual(normalize_symbol("XAGUSD"), "SI=F")

    def test_energy_and_index_aliases(self):
        self.assertEqual(normalize_symbol("USOIL"), "CL=F")
        self.assertEqual(normalize_symbol("SPX500"), "^GSPC")
        self.assertEqual(normalize_symbol("NAS100"), "^NDX")
        self.assertEqual(normalize_symbol("US30"), "^DJI")

    def test_forex_pairs_get_x_suffix(self):
        self.assertEqual(normalize_symbol("EURUSD"), "EURUSD=X")
        self.assertEqual(normalize_symbol("GBPJPY"), "GBPJPY=X")
        self.assertEqual(normalize_symbol("eurusd"), "EURUSD=X")

    def test_crypto_pairs_get_dash_usd(self):
        self.assertEqual(normalize_symbol("BTCUSD"), "BTC-USD")
        self.assertEqual(normalize_symbol("ETHUSD"), "ETH-USD")

    def test_six_letter_non_currency_left_alone(self):
        # GOOGLE-style 6-letter tickers that aren't two currency codes
        # must not be mangled into a fake forex pair.
        self.assertEqual(normalize_symbol("ABCDEF"), "ABCDEF")

    def test_empty_input_passthrough(self):
        self.assertEqual(normalize_symbol(""), "")

    def test_av_suffixes_canonicalize_to_yahoo(self):
        # Known Alpha Vantage exchange suffixes are canonicalized to the Yahoo
        # dialect at entry — the pipeline's internal dialect. All-AV setups
        # keep working because the vendor router translates back to the AV
        # dialect at dispatch time (see interface.py).
        for av, yahoo in (
            ("ADS.FRK", "ADS.F"),
            ("ADS.DEX", "ADS.DE"),
            ("MBG.FRK", "MBG.F"),
            ("TSCO.LON", "TSCO.L"),
            ("RELIANCE.BSE", "RELIANCE.BO"),
            ("SHOP.TRT", "SHOP.TO"),
        ):
            self.assertEqual(normalize_symbol(av), yahoo)
        self.assertEqual(normalize_symbol("ads.dex"), "ADS.DE")  # case-insensitive

    def test_yahoo_and_unknown_suffixes_pass_through(self):
        # Yahoo-native suffixes, share classes, and unknown suffixes stay
        # untouched — only suffixes in the AV table are translated.
        for sym in ("ADS.DE", "TSCO.L", "0700.HK", "BRK.B", "FOO.XYZ",
                    "^GSPC", "GC=F"):
            self.assertEqual(normalize_symbol(sym), sym)

    def test_dialect_helpers_roundtrip_and_guards(self):
        self.assertEqual(yahoo_symbol_to_av("MBG.F"), "MBG.FRK")
        self.assertEqual(yahoo_symbol_to_av("RELIANCE.BO"), "RELIANCE.BSE")
        self.assertEqual(av_symbol_to_yahoo(yahoo_symbol_to_av("TSCO.L")), "TSCO.L")
        # No suffix / unknown suffix / share class: no-op in both directions.
        self.assertEqual(yahoo_symbol_to_av("AAPL"), "AAPL")
        self.assertEqual(av_symbol_to_yahoo("AAPL"), "AAPL")
        self.assertEqual(yahoo_symbol_to_av("BRK.B"), "BRK.B")
        self.assertEqual(av_symbol_to_yahoo("BRK.B"), "BRK.B")
        # Empty base (".DEX") must not be mangled.
        self.assertEqual(av_symbol_to_yahoo(".DEX"), ".DEX")


@pytest.mark.unit
class TestNoMarketDataError(unittest.TestCase):
    def test_message_includes_resolution(self):
        err = NoMarketDataError("XAUUSD+", "GC=F", "no rows")
        self.assertIn("XAUUSD+", str(err))
        self.assertIn("GC=F", str(err))
        self.assertEqual(err.symbol, "XAUUSD+")
        self.assertEqual(err.canonical, "GC=F")

    def test_canonical_defaults_to_symbol(self):
        err = NoMarketDataError("FOOBAR")
        self.assertEqual(err.canonical, "FOOBAR")


@pytest.mark.unit
class TestIsYahooSafe(unittest.TestCase):
    def test_accepts_structural_chars(self):
        for sym in ("AAPL", "GC=F", "^GSPC", "BRK.B", "BTC-USD"):
            self.assertTrue(is_yahoo_safe(sym))

    def test_rejects_slash_and_space(self):
        for sym in ("a/b", "AA PL", ""):
            self.assertFalse(is_yahoo_safe(sym))


@pytest.mark.unit
class TestCryptoBase(unittest.TestCase):
    def test_resolves_known_crypto_forms(self):
        for raw in ("BTC-USD", "BTCUSD", "btc-usdt", "BTC-USDC", "BTCUSD+"):
            self.assertEqual(crypto_base(raw), "BTC")
        self.assertEqual(crypto_base("ETH-USD"), "ETH")
        self.assertEqual(crypto_base("sol-usd"), "SOL")

    def test_non_crypto_returns_none(self):
        # Plain equities, class shares, and real tickers that alias elsewhere
        # (GOLD -> gold future on the Yahoo path) must NOT read as crypto.
        for raw in ("AAPL", "BRK-B", "GOLD", "XYZ-USD", "EURUSD", "", None):
            self.assertIsNone(crypto_base(raw))

    def test_agrees_with_normalize_symbol(self):
        # crypto_base is the shared primitive behind the -USD normalization.
        self.assertEqual(normalize_symbol("BTCUSD"), "BTC-USD")
        self.assertEqual(crypto_base("BTCUSD"), "BTC")


if __name__ == "__main__":
    unittest.main()
