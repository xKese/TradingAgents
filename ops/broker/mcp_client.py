"""RobinhoodMCPClient protocol + typed DTOs.

Concrete implementations:
- RealRobinhoodMCPClient — production, wraps the mcp Python SDK.
- FakeMCPClient (tests/ops/broker/fakes.py) — in-memory, deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ops.broker.types import OrderType, Side


class MCPUnavailable(Exception):
    """Raised when the MCP endpoint fails (network, auth, protocol error)."""


@dataclass(frozen=True)
class AccountInfo:
    cash: Decimal
    equity: Decimal
    buying_power: Decimal


@dataclass(frozen=True)
class MCPPosition:
    symbol: str
    quantity: Decimal
    avg_price: Decimal


@dataclass(frozen=True)
class MCPOrderAck:
    order_id: str
    client_order_id: str
    symbol: str
    side: Side
    quantity: Decimal | None
    notional: Decimal | None
    status: str    # "queued" | "filled" | "rejected"
    fill_price: Decimal | None


@runtime_checkable
class RobinhoodMCPClient(Protocol):
    def get_account(self) -> AccountInfo: ...
    def get_positions(self) -> list[MCPPosition]: ...
    def get_quote(self, symbol: str) -> Decimal: ...
    def place_equity_order(
        self, *, symbol: str, side: Side,
        notional: Decimal | None, quantity: Decimal | None,
        order_type: OrderType, limit_price: Decimal | None,
        client_order_id: str,
    ) -> MCPOrderAck: ...
    def cancel_equity_order(self, order_id: str) -> None: ...
