"""Time-aware performance maths: XIRR, equity curve, and benchmark comparisons.

Pure functions, no I/O. Dates are ISO strings (``YYYY-MM-DD``).

Cash-flow convention (investor's point of view):
  money paid in (buys, incl. fees)          -> negative
  money received (sells net of fees, dividends, final holdings value) -> positive
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta
from typing import Iterable, Optional

from .portfolio import BUY, SELL, Dividend, Position, Transaction

Flow = tuple[str, float]  # (iso date, signed amount)


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


# --- XIRR -----------------------------------------------------------------------
def xirr(flows: Iterable[Flow]) -> Optional[float]:
    """Annualised money-weighted return (as a fraction, 0.12 = 12%/yr), or None.

    Needs at least one outflow and one inflow. Solved by bisection on the NPV, which is
    slower than Newton but cannot diverge.
    """
    items = [(_d(d), a) for d, a in flows if abs(a) > 1e-12]
    if not items or not any(a < 0 for _, a in items) or not any(a > 0 for _, a in items):
        return None
    t0 = min(d for d, _ in items)
    pts = [((d - t0).days / 365.0, a) for d, a in items]

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** t for t, a in pts)

    lo, hi = -0.999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    # Widen the upper bound for extreme gains before giving up.
    while f_lo * f_hi > 0 and hi < 1e4:
        hi *= 4
        f_hi = npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


# --- cash flows ------------------------------------------------------------------
def cash_flows(
    transactions: Iterable[Transaction],
    dividends: Iterable[Dividend] = (),
    symbol: Optional[str] = None,
) -> list[Flow]:
    """Signed cash flows from the ledger (zero-cost bonus buys contribute nothing)."""
    flows: list[Flow] = []
    for t in transactions:
        if symbol and t.symbol != symbol:
            continue
        if t.side == BUY:
            amount = -(t.quantity * t.price + t.fee)
        else:
            amount = t.quantity * t.price - t.fee
        if abs(amount) > 1e-12:
            flows.append((t.date, amount))
    for dv in dividends:
        if symbol and dv.symbol != symbol:
            continue
        if dv.amount:
            flows.append((dv.date, dv.amount))
    flows.sort(key=lambda f: f[0])
    return flows


def terminal_value(positions: Iterable[Position], symbol: Optional[str] = None) -> float:
    """What the open shares are worth now (market value, else cost basis if unpriced)."""
    total = 0.0
    for p in positions:
        if symbol and p.symbol != symbol:
            continue
        if p.quantity <= 0:
            continue
        total += p.market_value if p.market_value is not None else p.cost_basis_remaining
    return total


def xirr_with_terminal(flows: list[Flow], value_now: float, today: str) -> Optional[float]:
    return xirr(flows + ([(today, value_now)] if value_now > 0 else []))


# --- equity curve -----------------------------------------------------------------
def _price_lookup(history: dict[str, list[tuple[str, float]]]):
    """symbol -> (sorted dates, closes) for O(log n) 'last close on or before' lookups."""
    cache = {}
    for sym, pts in history.items():
        pts = sorted(pts)
        cache[sym] = ([p[0] for p in pts], [p[1] for p in pts])
    return cache


def equity_curve(
    transactions: list[Transaction],
    history: dict[str, list[tuple[str, float]]],
    end: Optional[str] = None,
) -> list[dict]:
    """Daily portfolio value vs. net cash contributed.

    value       = sum(shares held * last known close on/before that day)
    contributed = cumulative buys (incl. fees) - cumulative sell proceeds (net of fees)

    Prices fall back to the last traded price from the ledger when no history exists yet.
    Dividends are excluded: the gap between the lines is trading profit only.
    """
    if not transactions:
        return []
    txns = sorted(transactions, key=lambda t: (t.date, t.side == SELL))
    start, stop = _d(txns[0].date), _d(end) if end else date.today()
    lookup = _price_lookup(history)

    qty: dict[str, int] = {}
    last_trade_price: dict[str, float] = {}
    contributed = 0.0
    i, n = 0, len(txns)
    out = []
    day = start
    while day <= stop:
        iso = day.isoformat()
        while i < n and txns[i].date <= iso:
            t = txns[i]
            if t.side == BUY:
                qty[t.symbol] = qty.get(t.symbol, 0) + t.quantity
                contributed += t.quantity * t.price + t.fee
            else:
                sold = min(t.quantity, qty.get(t.symbol, 0))
                qty[t.symbol] = qty.get(t.symbol, 0) - sold
                contributed -= t.quantity * t.price - t.fee
            if t.price > 0:
                last_trade_price[t.symbol] = t.price
            i += 1
        value = 0.0
        for sym, q in qty.items():
            if q <= 0:
                continue
            px = None
            if sym in lookup:
                dates, closes = lookup[sym]
                k = bisect_right(dates, iso) - 1
                if k >= 0:
                    px = closes[k]
            if px is None:
                px = last_trade_price.get(sym, 0.0)
            value += q * px
        out.append({"date": iso, "value": value, "contributed": contributed})
        day += timedelta(days=1)
    return out


# --- benchmarks -------------------------------------------------------------------
def _close_on_or_before(points: list[tuple[str, float]], iso: str) -> Optional[float]:
    dates = [p[0] for p in points]
    k = bisect_right(dates, iso) - 1
    if k >= 0:
        return points[k][1]
    return points[0][1] if points else None  # before the series starts: use first close


def index_equivalent(flows: list[Flow], index_history: list[tuple[str, float]], today: str) -> Optional[dict]:
    """Same cash flows, invested in an index instead (public-market equivalent).

    Every outflow buys index units at that day's close; every inflow sells units.
    Returns the hypothetical value today and its XIRR.
    """
    pts = sorted(index_history)
    if not pts or not flows:
        return None
    units = 0.0
    for d, amount in flows:
        px = _close_on_or_before(pts, d)
        if not px:
            return None
        units += -amount / px
    last = _close_on_or_before(pts, today)
    if not last:
        return None
    value = units * last
    return {"value": value, "xirr": xirr(flows + ([(today, value)] if value > 0 else []))}


def deposit_equivalent(flows: list[Flow], annual_rate: float, today: str) -> Optional[dict]:
    """Same cash flows, but held in a deposit/T-bill compounding at ``annual_rate``."""
    if not flows:
        return None
    end = _d(today)
    value = sum(-a * (1.0 + annual_rate) ** (max((end - _d(d)).days, 0) / 365.0) for d, a in flows)
    return {"value": value, "xirr": annual_rate}


# --- per-stock summary ---------------------------------------------------------------
def holding_days(transactions: Iterable[Transaction], symbol: str, today: str) -> int:
    dates = [t.date for t in transactions if t.symbol == symbol and t.price > 0]
    return (_d(today) - _d(min(dates))).days if dates else 0
