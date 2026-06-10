"""Cost-basis computation for a stock portfolio.

The core idea: never store an "average" price. Store every transaction and
*compute* the metrics by replaying the ledger in chronological order. This lets
us show several views of the same position at once:

- ``broker_avg``      : weighted-average cost (what THNDR/most brokers show).
                        Buys move it; sells do not.
- ``pure_avg_buy``    : average of buy prices only, ignoring sells and fees.
- ``breakeven``       : net cash break-even = (money in - money out) / shares held.
                        Includes realized gains, so round-trip "fixing" trades
                        are honestly accounted for.
- ``realized_pnl``    : profit/loss already locked in by sells.
- ``unrealized_pnl``  : paper profit/loss on shares still held (needs a price).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

BUY = "buy"
SELL = "sell"


@dataclass(frozen=True)
class Transaction:
    """A single buy or sell. Quantity and price are always positive."""

    symbol: str
    side: str  # BUY or SELL
    quantity: int
    price: float
    fee: float = 0.0
    date: str = ""  # ISO date string, used only for ordering/display

    def __post_init__(self) -> None:
        if self.side not in (BUY, SELL):
            raise ValueError(f"side must be '{BUY}' or '{SELL}', got {self.side!r}")
        if not isinstance(self.quantity, int):
            raise ValueError("quantity must be an integer")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.price < 0:
            raise ValueError("price cannot be negative")
        if self.fee < 0:
            raise ValueError("fee cannot be negative")


@dataclass
class Position:
    """Computed metrics for one symbol."""

    symbol: str
    quantity: int = 0
    broker_avg: float = 0.0          # moving weighted-average cost (THNDR-style)
    pure_avg_buy: float = 0.0        # average buy price, ignoring sells/fees
    total_buy_cost: float = 0.0      # sum(qty*price + fee) over buys
    total_sell_proceeds: float = 0.0 # sum(qty*price - fee) over sells
    realized_pnl: float = 0.0        # locked-in profit from sells
    current_price: Optional[float] = None

    @property
    def net_invested(self) -> float:
        """Cash still tied up: money in minus money already taken out."""
        return self.total_buy_cost - self.total_sell_proceeds

    @property
    def breakeven(self) -> Optional[float]:
        """Price per share at which selling everything would net zero cash."""
        if self.quantity <= 0:
            return None
        return self.net_invested / self.quantity

    @property
    def cost_basis_remaining(self) -> float:
        """Book value of shares still held, using the broker average."""
        return self.broker_avg * self.quantity

    @property
    def market_value(self) -> Optional[float]:
        if self.current_price is None:
            return None
        return self.current_price * self.quantity

    @property
    def unrealized_pnl(self) -> Optional[float]:
        if self.current_price is None:
            return None
        return self.market_value - self.cost_basis_remaining

    @property
    def total_pnl(self) -> Optional[float]:
        if self.unrealized_pnl is None:
            return self.realized_pnl
        return self.realized_pnl + self.unrealized_pnl

    @property
    def unrealized_pct(self) -> Optional[float]:
        """Paper gain/loss vs. broker cost basis (the THNDR-style up/down %)."""
        if self.current_price is None or self.broker_avg <= 0:
            return None
        return (self.current_price - self.broker_avg) / self.broker_avg

    @property
    def breakeven_pct(self) -> Optional[float]:
        """Gain/loss vs. your true break-even (includes realized gains)."""
        be = self.breakeven
        if self.current_price is None or be is None or be <= 0:
            return None
        return (self.current_price - be) / be

    @property
    def total_return_pct(self) -> Optional[float]:
        """Total P&L (realized + unrealized) as a % of all money you put in."""
        if self.total_pnl is None or self.total_buy_cost <= 0:
            return None
        return self.total_pnl / self.total_buy_cost


def _replay(symbol: str, txns: list[Transaction]) -> Position:
    """Replay one symbol's transactions in chronological order."""
    pos = Position(symbol=symbol)
    buy_qty_total = 0
    buy_value_total = 0.0  # sum(qty*price), no fees, for pure average

    for t in txns:
        if t.side == BUY:
            cost = t.quantity * t.price + t.fee
            # Moving weighted-average cost, including fees.
            new_qty = pos.quantity + t.quantity
            pos.broker_avg = (pos.cost_basis_remaining + cost) / new_qty if new_qty else 0.0
            pos.quantity = new_qty
            pos.total_buy_cost += cost
            buy_qty_total += t.quantity
            buy_value_total += t.quantity * t.price
        else:  # SELL
            sell_qty = min(t.quantity, pos.quantity)  # guard against overselling
            proceeds = t.quantity * t.price - t.fee
            # Realized P&L = proceeds vs. cost basis of the shares sold.
            pos.realized_pnl += proceeds - pos.broker_avg * sell_qty
            pos.quantity -= sell_qty
            pos.total_sell_proceeds += proceeds
            # broker_avg deliberately unchanged on a sell.

    pos.pure_avg_buy = buy_value_total / buy_qty_total if buy_qty_total else 0.0
    return pos


def compute_positions(
    transactions: Iterable[Transaction],
    prices: Optional[dict[str, float]] = None,
) -> list[Position]:
    """Compute one Position per symbol from a flat list of transactions.

    ``prices`` maps symbol -> current market price for unrealized P&L.
    Transactions are sorted by date so the moving average replays correctly.
    """
    prices = prices or {}
    by_symbol: dict[str, list[Transaction]] = {}
    for t in transactions:
        by_symbol.setdefault(t.symbol, []).append(t)

    positions: list[Position] = []
    for symbol, txns in by_symbol.items():
        ordered = sorted(txns, key=lambda x: (x.date, x.side == SELL))
        pos = _replay(symbol, ordered)
        if symbol in prices:
            pos.current_price = prices[symbol]
        positions.append(pos)

    positions.sort(key=lambda p: p.symbol)
    return positions
