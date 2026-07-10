"""Read-only Interactive Brokers portfolio context for decision agents."""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


class IBKRPortfolioError(RuntimeError):
    """Raised when a trustworthy TWS portfolio snapshot cannot be produced."""


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_account_summary(items: list[Any]) -> dict[str, Any]:
    wanted = {
        "NetLiquidation": "net_liquidation",
        "TotalCashValue": "cash",
        "GrossPositionValue": "gross_position_value",
        "AvailableFunds": "available_funds",
        "BuyingPower": "buying_power",
    }
    values: dict[str, Any] = {}
    currencies: list[str] = []
    for item in items:
        key = wanted.get(getattr(item, "tag", ""))
        if not key:
            continue
        values[key] = _number(getattr(item, "value", None))
        currency = str(getattr(item, "currency", "") or "").strip()
        if currency and currency.upper() not in {"BASE", "ALL"}:
            currencies.append(currency.upper())
    values["base_currency"] = currencies[0] if currencies else "UNKNOWN"
    return values


def _position_to_dict(item: Any, net_liquidation: float | None) -> dict[str, Any]:
    contract = item.contract
    symbol = str(getattr(contract, "localSymbol", "") or getattr(contract, "symbol", ""))
    market_value = _number(getattr(item, "marketValue", None))
    weight = None
    if market_value is not None and net_liquidation not in (None, 0):
        weight = market_value / net_liquidation * 100
    quantity = _number(getattr(item, "position", None))
    average_cost = _number(getattr(item, "averageCost", None))
    unrealized_pnl = _number(getattr(item, "unrealizedPNL", None))
    cost_basis = None
    unrealized_return_pct = None
    if quantity is not None and average_cost is not None:
        cost_basis = abs(quantity) * average_cost
    if unrealized_pnl is not None and cost_basis:
        unrealized_return_pct = unrealized_pnl / cost_basis * 100
    return {
        "symbol": symbol.upper(),
        "contract_symbol": str(getattr(contract, "symbol", "") or "").upper(),
        "quantity": quantity,
        "currency": str(getattr(contract, "currency", "") or "UNKNOWN").upper(),
        "market_price": _number(getattr(item, "marketPrice", None)),
        "market_value": market_value,
        "average_cost": average_cost,
        "unrealized_pnl": unrealized_pnl,
        "unrealized_return_pct": unrealized_return_pct,
        "portfolio_weight_pct": weight,
    }


def validate_portfolio_snapshot(snapshot: dict[str, Any]) -> None:
    """Reject snapshots whose empty holdings contradict account exposure."""
    gross = snapshot.get("gross_position_value")
    if gross not in (None, 0) and not snapshot.get("positions"):
        raise IBKRPortfolioError(
            "TWS reported a nonzero gross position value but returned no stock positions"
        )


def sanitize_portfolio_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a serializable copy containing no account identity or connection data."""
    clean = deepcopy(snapshot)
    for key in (
        "account",
        "account_id",
        "username",
        "token",
        "host",
        "port",
        "client_id",
    ):
        clean.pop(key, None)
    return clean


def load_portfolio_snapshot(
    host: str,
    port: int,
    client_id: int,
    timeout: float = 10.0,
    *,
    ib_factory=None,
) -> dict[str, Any]:
    """Load one complete stock-portfolio snapshot from TWS in read-only mode."""
    if ib_factory is None:
        from ib_async import IB

        ib_factory = IB
    ib = ib_factory()
    try:
        ib.connect(
            host,
            port,
            clientId=client_id,
            timeout=timeout,
            readonly=True,
        )
        accounts = list(ib.managedAccounts())
        if len(accounts) != 1:
            raise IBKRPortfolioError("TWS must expose exactly one managed account")
        account = accounts[0]
        summary = _parse_account_summary(list(ib.accountSummary(account)))
        net_liquidation = summary.get("net_liquidation")
        positions = [
            _position_to_dict(item, net_liquidation)
            for item in ib.portfolio(account)
            if str(getattr(item.contract, "secType", "")).upper() == "STK"
        ]
        positions.sort(
            key=lambda value: abs(value.get("market_value") or 0), reverse=True
        )
        snapshot = {
            **summary,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "position_fetch_complete": True,
            "positions": positions,
        }
        validate_portfolio_snapshot(snapshot)
        return sanitize_portfolio_snapshot(snapshot)
    except IBKRPortfolioError:
        raise
    except Exception as exc:
        raise IBKRPortfolioError(f"Unable to load TWS portfolio: {exc}") from exc
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


def _money(value: Any, currency: str) -> str:
    number = _number(value)
    return "unavailable" if number is None else f"{currency} {number:,.2f}"


def render_portfolio_context(snapshot: dict[str, Any], ticker: str) -> str:
    """Render concise account and ticker context for decision-stage prompts."""
    requested = ticker.strip().upper()
    positions = list(snapshot.get("positions") or [])
    exact = [
        position
        for position in positions
        if requested in {position.get("symbol"), position.get("contract_symbol")}
    ]
    currency = snapshot.get("base_currency", "UNKNOWN")
    lines = [
        "LIVE PORTFOLIO CONTEXT - READ ONLY",
        f"Account NAV: {_money(snapshot.get('net_liquidation'), currency)}",
        f"Cash: {_money(snapshot.get('cash'), currency)}",
        f"Available funds: {_money(snapshot.get('available_funds'), currency)}",
        f"Ticker: {requested}",
        "Position fetch complete: "
        + ("yes" if snapshot.get("position_fetch_complete") else "no"),
    ]
    if len(exact) == 1:
        position = exact[0]
        rank = positions.index(position) + 1
        weight = position.get("portfolio_weight_pct")
        lines.extend(
            [
                "Owned: yes",
                f"Quantity: {position.get('quantity'):g}",
                f"Average cost: {_money(position.get('average_cost'), position.get('currency', 'UNKNOWN'))}",
                f"Current price: {_money(position.get('market_price'), position.get('currency', 'UNKNOWN'))}",
                f"Unrealized P&L: {_money(position.get('unrealized_pnl'), position.get('currency', 'UNKNOWN'))}",
                "Current portfolio weight: "
                + ("unavailable" if weight is None else f"{weight:.2f}%"),
                f"Position rank by market value: {rank} of {len(positions)}",
            ]
        )
    elif len(exact) > 1:
        lines.extend(["Owned: uncertain", "Reason: multiple IBKR positions matched ticker"])
    elif snapshot.get("position_fetch_complete"):
        lines.append("Owned: no")
    else:
        lines.append("Owned: uncertain")
    if positions:
        lines.append("Largest positions:")
        for position in positions[:5]:
            weight = position.get("portfolio_weight_pct")
            weight_text = "unavailable" if weight is None else f"{weight:.2f}%"
            lines.append(f"- {position.get('symbol')}: {weight_text}")
    return "\n".join(lines)


def get_portfolio_context_from_state(state: dict[str, Any]) -> str:
    """Return ticker-specific portfolio context from a graph state."""
    snapshot = state.get("portfolio_context") or {}
    if not snapshot:
        return ""
    return render_portfolio_context(snapshot, state["company_of_interest"])
