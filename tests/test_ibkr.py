from types import SimpleNamespace

import pytest

from tradingagents.ibkr import (
    IBKRPortfolioError,
    load_portfolio_snapshot,
    render_portfolio_context,
)


class FakeIB:
    last_instance = None

    def __init__(self):
        type(self).last_instance = self
        self.connect_args = None
        self.disconnected = False

    def connect(self, host, port, clientId, timeout, readonly):
        self.connect_args = (host, port, clientId, timeout, readonly)
        return self

    def managedAccounts(self):
        return ["SECRET_ACCOUNT"]

    def accountSummary(self, account):
        assert account == "SECRET_ACCOUNT"
        return [
            SimpleNamespace(tag="NetLiquidation", value="7436.90", currency="AUD"),
            SimpleNamespace(tag="TotalCashValue", value="2582.97", currency="AUD"),
            SimpleNamespace(tag="GrossPositionValue", value="4853.93", currency="AUD"),
            SimpleNamespace(tag="AvailableFunds", value="5791.39", currency="AUD"),
        ]

    def portfolio(self, account):
        assert account == "SECRET_ACCOUNT"
        contract = SimpleNamespace(
            symbol="OUST", localSymbol="OUST", secType="STK", currency="USD"
        )
        return [
            SimpleNamespace(
                contract=contract,
                position=10,
                marketPrice=47.82,
                marketValue=478.20,
                averageCost=51.653,
                unrealizedPNL=-38.33,
            )
        ]

    def disconnect(self):
        self.disconnected = True


def test_load_snapshot_is_read_only_sanitized_and_disconnected():
    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=FakeIB
    )

    instance = FakeIB.last_instance
    assert instance.connect_args == ("127.0.0.1", 7496, 71, 10.0, True)
    assert instance.disconnected is True
    assert snapshot["base_currency"] == "AUD"
    assert snapshot["net_liquidation"] == 7436.9
    assert snapshot["positions"][0]["symbol"] == "OUST"
    assert snapshot["positions"][0]["quantity"] == 10
    assert "SECRET_ACCOUNT" not in repr(snapshot)


def test_multiple_accounts_fail_and_disconnect():
    class MultipleAccounts(FakeIB):
        def managedAccounts(self):
            return ["A", "B"]

    with pytest.raises(IBKRPortfolioError, match="exactly one"):
        load_portfolio_snapshot("127.0.0.1", 7496, 71, ib_factory=MultipleAccounts)

    assert MultipleAccounts.last_instance.disconnected is True


def test_nonzero_gross_value_with_no_positions_fails():
    class MissingPositions(FakeIB):
        def portfolio(self, account):
            return []

    with pytest.raises(IBKRPortfolioError, match="nonzero gross position value"):
        load_portfolio_snapshot("127.0.0.1", 7496, 71, ib_factory=MissingPositions)


def test_missing_market_price_keeps_quantity_and_marks_weight_unavailable():
    class MissingPrice(FakeIB):
        def portfolio(self, account):
            item = super().portfolio(account)[0]
            item.marketPrice = float("nan")
            item.marketValue = float("nan")
            return [item]

    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=MissingPrice
    )
    position = snapshot["positions"][0]
    assert position["quantity"] == 10
    assert position["average_cost"] == 51.653
    assert position["market_price"] is None
    assert position["portfolio_weight_pct"] is None


def test_render_owned_ticker_includes_position_and_concentration():
    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=FakeIB
    )
    rendered = render_portfolio_context(snapshot, "OUST")

    assert "LIVE PORTFOLIO CONTEXT - READ ONLY" in rendered
    assert "Owned: yes" in rendered
    assert "Quantity: 10" in rendered
    assert "Current portfolio weight: 65.27%" in rendered
    assert "Position rank by market value: 1 of 1" in rendered


def test_render_absent_ticker_only_says_unowned_after_complete_fetch():
    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=FakeIB
    )
    rendered = render_portfolio_context(snapshot, "CCXI")

    assert "Ticker: CCXI" in rendered
    assert "Owned: no" in rendered
    assert "Position fetch complete: yes" in rendered


def test_connection_error_is_wrapped_without_leaking_account_data():
    class BrokenIB(FakeIB):
        def connect(self, *args, **kwargs):
            raise OSError("connection refused")

    with pytest.raises(IBKRPortfolioError, match="Unable to load TWS portfolio"):
        load_portfolio_snapshot("127.0.0.1", 7496, 71, ib_factory=BrokenIB)

    assert BrokenIB.last_instance.disconnected is True


def test_weights_reconcile_quote_currency_positions_to_base_gross_exposure():
    class CurrencyScaledIB(FakeIB):
        def accountSummary(self, account):
            return [
                SimpleNamespace(tag="NetLiquidation", value="3000", currency="AUD"),
                SimpleNamespace(tag="TotalCashValue", value="1500", currency="AUD"),
                SimpleNamespace(tag="GrossPositionValue", value="1500", currency="AUD"),
            ]

        def portfolio(self, account):
            first = SimpleNamespace(
                contract=SimpleNamespace(
                    symbol="AAA", localSymbol="AAA", secType="STK", currency="USD"
                ),
                position=6,
                marketPrice=100,
                marketValue=600,
                averageCost=90,
                unrealizedPNL=60,
            )
            second = SimpleNamespace(
                contract=SimpleNamespace(
                    symbol="BBB", localSymbol="BBB", secType="STK", currency="USD"
                ),
                position=4,
                marketPrice=100,
                marketValue=400,
                averageCost=90,
                unrealizedPNL=40,
            )
            return [first, second]

    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=CurrencyScaledIB
    )

    assert snapshot["positions"][0]["portfolio_weight_pct"] == pytest.approx(30)
    assert snapshot["positions"][1]["portfolio_weight_pct"] == pytest.approx(20)


def test_same_currency_weights_are_marked_reconciled_to_base_nav():
    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=FakeIB
    )

    assert snapshot["weights_reconciled_to_base_nav"] is True


def test_mixed_currency_weights_are_not_marked_reconciled():
    class MixedCurrencyIB(FakeIB):
        def portfolio(self, account):
            usd = super().portfolio(account)[0]
            eur = SimpleNamespace(
                contract=SimpleNamespace(
                    symbol="SAP", localSymbol="SAP", secType="STK", currency="EUR"
                ),
                position=1,
                marketPrice=200,
                marketValue=200,
                averageCost=180,
                unrealizedPNL=20,
            )
            return [usd, eur]

    snapshot = load_portfolio_snapshot(
        "127.0.0.1", 7496, 71, ib_factory=MixedCurrencyIB
    )

    assert snapshot["weights_reconciled_to_base_nav"] is False
