"""Pure helpers for sector concentration, drawdowns, DCA plans and benchmark comparisons.

No Streamlit or database imports here so everything is unit-testable. Persisted data (sector
overrides, DCA plans) is stored as JSON strings in the generic ``settings`` table.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from datetime import date, timedelta
from typing import Iterable, Mapping, Optional

from . import companies

SETTING_SECTOR_OVERRIDES = "sector_overrides"
SETTING_DCA_PLANS = "dca_plans"
SETTING_SECTOR_LIMIT = "sector_limit"
DEFAULT_SECTOR_LIMIT = 40.0

FREQ_DAYS = {"Weekly": 7, "Every 2 weeks": 14, "Monthly": 30}


# --- JSON settings ---------------------------------------------------------------------
def _load_json(raw: object, default):
    try:
        data = json.loads(str(raw))
    except (TypeError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def load_sector_overrides(raw: object) -> dict[str, str]:
    data = _load_json(raw, {})
    return {str(k): str(v) for k, v in data.items() if str(v).strip()}


def dump_sector_overrides(overrides: Mapping[str, str]) -> str:
    return json.dumps({k: v for k, v in overrides.items() if v and v.strip()}, sort_keys=True)


def load_dca_plans(raw: object) -> dict[str, dict]:
    data = _load_json(raw, {})
    out = {}
    for sym, plan in data.items():
        try:
            out[str(sym)] = {
                "amount": float(plan["amount"]),
                "frequency": plan["frequency"] if plan["frequency"] in FREQ_DAYS else "Monthly",
                "start": str(plan.get("start") or ""),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def dump_dca_plans(plans: Mapping[str, dict]) -> str:
    return json.dumps(plans, sort_keys=True)


# --- sectors ---------------------------------------------------------------------------
def sector_for(symbol: str, funds: Iterable[str] = (), overrides: Optional[Mapping[str, str]] = None) -> str:
    if symbol in set(funds):
        return companies.FUND_SECTOR
    return companies.sector_of(symbol, overrides)


def aggregate_sectors(
    entries: Iterable[tuple[str, float, float]],
    funds: Iterable[str] = (),
    overrides: Optional[Mapping[str, str]] = None,
) -> list[dict]:
    """entries = (symbol, market_value, cost_basis). Returns sectors sorted by weight desc."""
    entries = list(entries)
    funds = set(funds)
    total_v = sum(v for _, v, _ in entries) or 1.0
    total_c = sum(c for _, _, c in entries) or 1.0
    agg: dict[str, dict] = {}
    for sym, value, cost in entries:
        name = sector_for(sym, funds, overrides)
        a = agg.setdefault(name, {"sector": name, "value": 0.0, "cost": 0.0, "symbols": []})
        a["value"] += value
        a["cost"] += cost
        a["symbols"].append(sym)
    out = list(agg.values())
    for a in out:
        a["w"] = a["value"] / total_v * 100
        a["cw"] = a["cost"] / total_c * 100
        a["ret"] = (a["value"] / a["cost"] - 1) * 100 if a["cost"] else None
    out.sort(key=lambda a: -a["w"])
    return out


def sector_share_after_buy(
    values: Mapping[str, float],
    symbol: str,
    value_before: float,
    value_after: float,
    funds: Iterable[str] = (),
    overrides: Optional[Mapping[str, str]] = None,
) -> dict:
    """Sector share of the wallet before/after a buy.

    ``values`` = market value of every held symbol. ``symbol`` is valued at ``value_before`` /
    ``value_after`` (its own entry in ``values`` is ignored); everything else stays as is.
    """
    funds = set(funds)
    sector = sector_for(symbol, funds, overrides)
    mates = [s for s in values if s != symbol and sector_for(s, funds, overrides) == sector]
    mates_value = sum(values[s] for s in mates)
    others = sum(v for s, v in values.items() if s != symbol)
    wallet_before, wallet_after = others + value_before, others + value_after
    before = (mates_value + value_before) / wallet_before * 100 if wallet_before else 0.0
    after = (mates_value + value_after) / wallet_after * 100 if wallet_after else 0.0
    return {"sector": sector, "mates": mates, "before": before, "after": after}


def concentration_alerts(
    stock_rows: Iterable[dict], sector_rows: Iterable[dict], stock_limit: float, sector_limit: float,
    funds: Iterable[str] = (),
) -> list[str]:
    """Human-readable alerts for single stocks / sectors above their limits (funds exempt)."""
    funds = set(funds)
    msgs = []
    for r in stock_rows:
        sym = r["symbol"]
        if sym not in funds and r["w"] > stock_limit:
            msgs.append(f"{sym} is {r['w']:.1f}% of your wallet (limit {stock_limit:g}%).")
    for a in sector_rows:
        if a["sector"] != companies.FUND_SECTOR and a["w"] > sector_limit:
            msgs.append(
                f"{a['sector']} sector is {a['w']:.1f}% of your wallet (limit {sector_limit:g}%): "
                + ", ".join(a["symbols"]) + "."
            )
    return msgs


# --- drawdown --------------------------------------------------------------------------
def drawdown_stats(points: list[tuple[str, float]], current: Optional[float] = None) -> Optional[dict]:
    """Distance from the all-time high in ``points`` (sorted (iso_date, close)) and time underwater."""
    pts = sorted(p for p in points if p[1] and p[1] > 0)
    if not pts:
        return None
    last_date, last_close = pts[-1]
    now = current if current else last_close
    high_date, high = max(pts, key=lambda p: (p[1], p[0]))
    if now > high:
        high = now
        high_date = last_date
    try:
        days = (date.fromisoformat(last_date) - date.fromisoformat(high_date)).days
    except ValueError:
        days = None
    return {
        "high": high, "high_date": high_date, "current": now,
        "from_high_pct": (now / high - 1) * 100,
        "days_since_high": days if now < high else 0,
    }


# --- DCA plans -------------------------------------------------------------------------
def dca_plan_status(plan: Mapping, last_buy_iso: Optional[str], today: date, tolerance_days: int = 3) -> dict:
    """When is the next buy due, and is the plan on track?

    status: 'not started' | 'due' | 'overdue' | 'on track'
    """
    every = FREQ_DAYS.get(plan.get("frequency", "Monthly"), 30)
    start = None
    try:
        start = date.fromisoformat(plan["start"]) if plan.get("start") else None
    except ValueError:
        start = None
    last = None
    try:
        last = date.fromisoformat(last_buy_iso) if last_buy_iso else None
    except ValueError:
        last = None
    if start and start > today and (last is None or last < start):
        return {"status": "not started", "next_due": start, "days_to_due": (start - today).days}
    anchor = last if last and (start is None or last >= start) else start
    if anchor is None:
        return {"status": "due", "next_due": today, "days_to_due": 0}
    due = anchor + timedelta(days=every)
    delta = (due - today).days
    if delta > 0:
        status = "on track"
    elif delta >= -tolerance_days:
        status = "due"
    else:
        status = "overdue"
    return {"status": status, "next_due": due, "days_to_due": delta}


def last_buy_date(txns: Iterable, symbol: str) -> Optional[str]:
    dates = [t.date for t in txns if t.symbol == symbol and t.side == "buy"]
    return max(dates) if dates else None


def planned_vs_actual(txns: Iterable, symbol: str, plan: Mapping, since_iso: str) -> dict:
    """Total bought in ``symbol`` since ``since_iso`` vs what the plan called for."""
    bought = sum(t.quantity * t.price + t.fee for t in txns
                 if t.symbol == symbol and t.side == "buy" and t.date >= since_iso)
    n_buys = sum(1 for t in txns if t.symbol == symbol and t.side == "buy" and t.date >= since_iso)
    return {"bought": bought, "buys": n_buys}


# --- benchmark comparison -----------------------------------------------------------------
def _close_on_or_before(points: list[tuple[str, float]], iso: str) -> Optional[float]:
    dates = [p[0] for p in points]
    k = bisect_right(dates, iso) - 1
    if k >= 0:
        return points[k][1]
    return points[0][1] if points else None


def rebased_comparison(
    nw_series: list[dict], index_points: list[tuple[str, float]], key: str = "total_egp"
) -> list[dict]:
    """Net worth and an index, both rebased to 100 at the first snapshot date."""
    series = [(str(s["date"])[:10], float(s[key])) for s in nw_series if float(s.get(key) or 0) > 0]
    pts = sorted(index_points)
    if not series or not pts:
        return []
    base_nw = series[0][1]
    base_ix = _close_on_or_before(pts, series[0][0])
    if not base_ix:
        return []
    out = []
    for d, v in series:
        ix = _close_on_or_before(pts, d)
        if ix:
            out.append({"date": d, "Net worth": v / base_nw * 100, "EGX30": ix / base_ix * 100})
    return out


def modified_dietz_ytd(curve: list[dict], year_start_iso: str) -> Optional[dict]:
    """Year-to-date profit and Modified Dietz return from ``performance.equity_curve`` output.

    If the portfolio began after year_start the return is measured from its first day.
    """
    days = [r for r in curve if r["date"] >= year_start_iso] or []
    if len(days) < 2:
        return None
    before = [r for r in curve if r["date"] < year_start_iso]
    start_row = before[-1] if before else days[0]
    end_row = days[-1]
    d0, d1 = date.fromisoformat(start_row["date"]), date.fromisoformat(end_row["date"])
    span = (d1 - d0).days
    if span <= 0:
        return None
    flows = 0.0
    weighted = 0.0
    prev_c = start_row["contributed"]
    for r in curve:
        if r["date"] <= start_row["date"] or r["date"] > end_row["date"]:
            continue
        f = r["contributed"] - prev_c
        prev_c = r["contributed"]
        if f:
            flows += f
            w = (d1 - date.fromisoformat(r["date"])).days / span
            weighted += f * w
    profit = end_row["value"] - start_row["value"] - flows
    base = start_row["value"] + weighted
    return {
        "profit": profit, "return_pct": profit / base * 100 if base > 0 else None,
        "start": start_row["date"], "end": end_row["date"],
    }


def index_return_pct(points: list[tuple[str, float]], start_iso: str, end_iso: Optional[str] = None) -> Optional[float]:
    pts = sorted(points)
    if not pts:
        return None
    first = _close_on_or_before(pts, start_iso)
    last = pts[-1][1] if end_iso is None else _close_on_or_before(pts, end_iso)
    if not first or not last:
        return None
    return (last / first - 1) * 100


def alpha_series(
    curve: list[dict], index_points: list[tuple[str, float]], start_iso: str
) -> list[dict]:
    """Cumulative portfolio vs index return from ``start_iso``, plus the alpha between them.

    ``curve`` is ``performance.equity_curve`` output. The portfolio uses a chained time-weighted
    growth factor (deposits/withdrawals aren't gains), the index is carried over non-trading days.
    Each row: date, portfolio_pct, index_pct, alpha_pp (all cumulative since the first row >= start).
    """
    pts = sorted(index_points)
    if not curve or not pts:
        return []
    growth: dict[str, float] = {}
    g, prev_v, prev_c = 1.0, 0.0, 0.0
    for r in curve:
        v, c = float(r["value"]), float(r["contributed"])
        if prev_v > 0:
            g *= (v - (c - prev_c)) / prev_v
        prev_v, prev_c = v, c
        growth[r["date"]] = g
    days = [r["date"] for r in curve if r["date"] >= start_iso and float(r["value"]) > 0]
    if len(days) < 2:
        return []
    g0, x0 = growth[days[0]], _close_on_or_before(pts, days[0])
    if not g0 or not x0:
        return []
    out = []
    for d in days:
        x = _close_on_or_before(pts, d)
        if not x:
            continue
        p_pct, i_pct = (growth[d] / g0 - 1) * 100, (x / x0 - 1) * 100
        out.append({"date": d, "portfolio_pct": p_pct, "index_pct": i_pct, "alpha_pp": p_pct - i_pct})
    return out


# --- rebalancing -----------------------------------------------------------------------
def rebalance_plan(
    values: Mapping[str, float],
    funds: Iterable[str] = (),
    overrides: Optional[Mapping[str, str]] = None,
    sector_limit: float = DEFAULT_SECTOR_LIMIT,
    stock_limit: float = 20.0,
) -> dict:
    """What to trim to get back under the limits, and where there's room to add.

    ``values`` = market value per held symbol (funds included in the wallet total, but never
    flagged). Trims assume the proceeds stay invested elsewhere, so the wallet total is unchanged:
    ``excess = value - limit% * total``. ``dilute_cash`` is the alternative: new money that, added
    to other holdings, would bring the item under its limit without selling anything.
    """
    funds = set(funds)
    total = sum(values.values())
    if total <= 0:
        return {"total": 0.0, "overs": [], "sector_room": {}, "stock_room": {}}

    def trims_for(symbols: list[str], amount: float) -> list[dict]:
        out, left = [], amount
        for s in sorted(symbols, key=lambda x: -values[x]):
            take = min(left, values[s])
            if take > 0:
                out.append({"symbol": s, "amount": take})
                left -= take
            if left <= 1e-9:
                break
        return out

    overs = []
    sectors: dict[str, list[str]] = {}
    for s in values:
        sectors.setdefault(sector_for(s, funds, overrides), []).append(s)
    sec_val = {k: sum(values[s] for s in v) for k, v in sectors.items()}

    for name, v in sec_val.items():
        cap = sector_limit / 100 * total
        if name != companies.FUND_SECTOR and v > cap + 1e-9:
            held = [s for s in sectors[name] if s not in funds]
            overs.append({
                "kind": "sector", "name": name, "value": v, "weight": v / total * 100,
                "limit": sector_limit, "excess": v - cap,
                "dilute_cash": v / (sector_limit / 100) - total,
                "trims": trims_for(held, v - cap), "symbols": held,
            })
    for s, v in values.items():
        cap = stock_limit / 100 * total
        if s not in funds and v > cap + 1e-9:
            overs.append({
                "kind": "stock", "name": s, "value": v, "weight": v / total * 100,
                "limit": stock_limit, "excess": v - cap,
                "dilute_cash": v / (stock_limit / 100) - total,
                "trims": [{"symbol": s, "amount": v - cap}], "symbols": [s],
            })
    overs.sort(key=lambda o: -o["weight"])

    sector_room = {
        k: max(0.0, sector_limit / 100 * total - v)
        for k, v in sec_val.items() if k != companies.FUND_SECTOR
    }
    stock_room = {
        s: max(0.0, stock_limit / 100 * total - v) for s, v in values.items() if s not in funds
    }
    return {"total": total, "overs": overs, "sector_room": sector_room, "stock_room": stock_room}


def allocate_cash(rooms: Mapping[str, float], cash: float) -> dict[str, float]:
    """Split ``cash`` across targets in proportion to their headroom, never exceeding it."""
    rooms = {k: v for k, v in rooms.items() if v > 0}
    out = {k: 0.0 for k in rooms}
    left = cash
    for _ in range(len(rooms) + 1):
        active = {k: v - out[k] for k, v in rooms.items() if v - out[k] > 1e-9}
        total_room = sum(active.values())
        if left <= 1e-9 or total_room <= 0:
            break
        spent = 0.0
        for k, room in active.items():
            part = min(room, left * room / total_room)
            out[k] += part
            spent += part
        left -= spent
    return {k: v for k, v in out.items() if v > 0}
