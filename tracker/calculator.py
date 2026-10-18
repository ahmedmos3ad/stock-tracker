"""What-if maths for averaging down (DCA) into an existing position.

Pure functions, no I/O. The formulas mirror ``portfolio._replay`` so the numbers
agree with the rest of the app:

- ``broker_avg`` : (old_avg * old_qty + buy_cost) / new_qty, fees included.
- ``breakeven``  : (net_invested + buy_cost) / new_qty, i.e. true cash break-even.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def _pct_vs(price: float, reference: Optional[float]) -> Optional[float]:
    """Gain/loss of ``price`` relative to ``reference`` (negative = loss)."""
    if reference is None or reference <= 0:
        return None
    return (price - reference) / reference


@dataclass(frozen=True)
class DcaResult:
    add_qty: int
    cost: float  # cash spent: add_qty * price + fee
    new_qty: int
    new_broker_avg: float
    new_breakeven: Optional[float]
    # Gain/loss at ``price`` before and after the buy (negative = loss).
    broker_pct_before: Optional[float]
    broker_pct_after: Optional[float]
    breakeven_pct_before: Optional[float]
    breakeven_pct_after: Optional[float]

    @property
    def broker_pct_improvement(self) -> Optional[float]:
        """Loss reduction in percentage points, vs the broker average."""
        if self.broker_pct_before is None or self.broker_pct_after is None:
            return None
        return self.broker_pct_after - self.broker_pct_before

    @property
    def breakeven_pct_improvement(self) -> Optional[float]:
        if self.breakeven_pct_before is None or self.breakeven_pct_after is None:
            return None
        return self.breakeven_pct_after - self.breakeven_pct_before


def simulate_dca(
    qty: int,
    broker_avg: float,
    net_invested: float,
    price: float,
    add_qty: int,
    fee: float = 0.0,
) -> DcaResult:
    """Simulate buying ``add_qty`` more shares at ``price`` (plus a flat ``fee``)."""
    if qty <= 0:
        raise ValueError("DCA needs an open position (qty > 0)")
    if price <= 0:
        raise ValueError("price must be positive")
    if add_qty < 0 or fee < 0:
        raise ValueError("add_qty and fee cannot be negative")

    cost = add_qty * price + (fee if add_qty else 0.0)
    new_qty = qty + add_qty
    new_broker_avg = (broker_avg * qty + cost) / new_qty
    old_breakeven = net_invested / qty
    new_breakeven = (net_invested + cost) / new_qty

    return DcaResult(
        add_qty=add_qty,
        cost=cost,
        new_qty=new_qty,
        new_broker_avg=new_broker_avg,
        new_breakeven=new_breakeven,
        broker_pct_before=_pct_vs(price, broker_avg),
        broker_pct_after=_pct_vs(price, new_broker_avg),
        breakeven_pct_before=_pct_vs(price, old_breakeven),
        breakeven_pct_after=_pct_vs(price, new_breakeven),
    )


# --- selling / trimming ---------------------------------------------------------------
@dataclass(frozen=True)
class SellResult:
    sell_qty: int
    proceeds: float  # cash received: sell_qty * price - fee
    realized_pnl: float  # proceeds minus the broker-average cost of the shares sold
    new_qty: int
    broker_avg: float  # unchanged by a sell (same rule as portfolio._replay)
    new_breakeven: Optional[float]  # None when the position is fully closed
    pct_at_price: Optional[float]  # gain/loss of the sale price vs broker avg


def simulate_sell(
    qty: int,
    broker_avg: float,
    net_invested: float,
    price: float,
    sell_qty: int,
    fee: float = 0.0,
) -> SellResult:
    """Simulate selling ``sell_qty`` shares at ``price`` (less a flat ``fee``)."""
    if qty <= 0:
        raise ValueError("selling needs an open position (qty > 0)")
    if price <= 0:
        raise ValueError("price must be positive")
    if not 0 < sell_qty <= qty:
        raise ValueError("sell_qty must be between 1 and the shares held")
    if fee < 0:
        raise ValueError("fee cannot be negative")

    proceeds = sell_qty * price - fee
    realized = proceeds - broker_avg * sell_qty
    new_qty = qty - sell_qty
    new_breakeven = (net_invested - proceeds) / new_qty if new_qty > 0 else None
    return SellResult(
        sell_qty=sell_qty,
        proceeds=proceeds,
        realized_pnl=realized,
        new_qty=new_qty,
        broker_avg=broker_avg,
        new_breakeven=new_breakeven,
        pct_at_price=_pct_vs(price, broker_avg),
    )


def shares_to_trim(qty: int, price: float, others_value: float, target_pct: float) -> int:
    """Shares to sell so the position is ``target_pct`` % of the wallet (open positions).

    Proceeds leave the wallet. Solves (q - s)*P / (others + (q - s)*P) = t for s,
    rounded up so the position ends at or below the target.
    """
    if not 0 < target_pct < 100:
        raise ValueError("target_pct must be between 0 and 100")
    t = target_pct / 100.0
    keep_value = t * others_value / (1.0 - t)  # value of the shares to keep
    keep = int(keep_value // price)  # whole shares we may keep
    return max(0, min(qty, qty - keep))


# --- brokerage fee ----------------------------------------------------------------------
DEFAULT_FEE_RATE = 0.00028  # ~0.028% of trade value, the usual fee on EGX trades


def estimate_fee(qty: float, price: float, rate: float = DEFAULT_FEE_RATE) -> float:
    """Estimated brokerage fee for a trade: ``rate`` x quantity x price, to the piastre."""
    return round(max(qty, 0) * max(price, 0) * rate, 2)


def fee_for_trade(qty: float, price: float, is_fund: bool = False) -> float:
    """Default fee for a trade: funds carry no fee, stocks use the standard estimate."""
    return 0.0 if is_fund else estimate_fee(qty, price)
