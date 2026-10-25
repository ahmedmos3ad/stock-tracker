"""Net-worth management insights: freshness, maturities, liquidity, income, concentration, targets,
and change attribution between snapshots.

Pure functions free of Streamlit so they can be unit-tested directly.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional

from .networth import (
    CAT_CD,
    CAT_FUND,
    CAT_LIABILITY,
    CAT_US_STOCK,
    NetWorthItem,
    item_currency,
    item_value_egp,
)
from .portfolio import Position, Transaction

# --- defaults -----------------------------------------------------------------------------------
STALE_PRICE_DAYS = 7       # USD rate, gold price, US stock price
STALE_BALANCE_DAYS = 30    # fund balances / NAVs, cash, CDs, debts
MATURITY_WARN_DAYS = 30
CONCENTRATION_LIMIT_PCT = 15.0

ALLOCATION_KEYS = ("stocks", "us_stocks", "funds", "gold", "fx", "cd_cash")
ALLOCATION_LABELS = {
    "stocks": "EGX Equities",
    "us_stocks": "US / Employer Stock",
    "funds": "Mutual Funds",
    "gold": "Gold",
    "fx": "FX Cash",
    "cd_cash": "CDs & Cash",
}


def _parse_day(value: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def age_days(updated_at: object, today: date) -> Optional[int]:
    """Whole days since ``updated_at`` (ISO date/timestamp), or ``None`` if missing/unparseable."""
    d = _parse_day(updated_at)
    return None if d is None else (today - d).days


# --- 3. stale prices / balances ------------------------------------------------------------------
def stale_inputs(
    settings: Mapping[str, str],
    items: Iterable[NetWorthItem],
    today: date,
    price_days: int = STALE_PRICE_DAYS,
    balance_days: int = STALE_BALANCE_DAYS,
) -> list[dict]:
    """Manual inputs that are older than their freshness window.

    Returns ``[{"label", "age" (days or None), "limit"}]`` sorted oldest first. Gold prices are only
    checked when gold is held, and the USD rate only when something is held in USD.
    """
    items = list(items)
    out: list[dict] = []

    def check(label: str, updated_at: object, limit: int) -> None:
        age = age_days(updated_at, today)
        if age is None or age > limit:
            out.append({"label": label, "age": age, "limit": limit})

    if any(item_currency(it) == "USD" for it in items):
        check("USD rate", settings.get("usd_egp_rate_updated_at"), price_days)
    if any(it.category == "gold" for it in items):
        check("Gold price", settings.get("gold_price_updated_at"), price_days)
    for it in items:
        if it.quantity <= 0:
            continue
        if it.category == CAT_US_STOCK:
            check(f"{it.name} share price", it.updated_at, price_days)
        elif it.category == CAT_FUND:
            check(f"{it.name} balance/NAV", it.updated_at, balance_days)
        elif it.category in ("cash", CAT_LIABILITY):
            check(f"{it.name} balance", it.updated_at, balance_days)
    out.sort(key=lambda r: -1 if r["age"] is None else -r["age"])
    return out


# --- 5. maturities -------------------------------------------------------------------------------
def maturity_schedule(
    items: Iterable[NetWorthItem],
    today: date,
    price_24k: float = 0.0,
    price_21k: float = 0.0,
    usd_egp_rate: float = 0.0,
) -> list[dict]:
    """CDs and debts with a maturity/payoff date, soonest first.

    Each row: ``{"item", "date", "days_left", "value_egp", "native", "currency", "is_liability"}``.
    """
    rows = []
    for it in items:
        if it.category not in (CAT_CD, CAT_LIABILITY) or it.quantity <= 0:
            continue
        d = _parse_day(it.maturity_date)
        if d is None:
            continue
        rows.append(
            {
                "item": it,
                "date": d,
                "days_left": (d - today).days,
                "value_egp": item_value_egp(it, price_24k, price_21k, usd_egp_rate),
                "native": it.quantity,
                "currency": item_currency(it),
                "is_liability": it.category == CAT_LIABILITY,
            }
        )
    rows.sort(key=lambda r: r["date"])
    return rows


def cds_without_maturity(items: Iterable[NetWorthItem]) -> list[NetWorthItem]:
    return [it for it in items if it.category == CAT_CD and it.quantity > 0 and not _parse_day(it.maturity_date)]


# --- 8. liquidity ----------------------------------------------------------------------------------
LIQUIDITY_TIERS = (
    ("cash", "💧 Cash — same day", "#1abc9c", "Cash accounts and foreign-currency cash"),
    ("funds", "🏦 Funds — 1–3 days", "#8c8cff", "Money-market / fixed-income mutual funds"),
    ("market", "📈 Market — sell first", "#e67e22", "EGX & US stocks and gold: quick to sell, but price can move"),
    ("locked", "🔒 Locked — until maturity", "#7f8c8d", "Certificates of deposit (breaking early costs interest)"),
)


def liquidity_breakdown(nw: dict) -> list[dict]:
    """Assets grouped by how quickly they can become spendable cash."""
    by_k = nw["by_key"]
    values = {
        "cash": float(nw.get("cash_egp", 0.0)) + float(by_k.get("fx", 0.0)),
        "funds": float(by_k.get("funds", 0.0)),
        "market": float(by_k.get("stocks", 0.0)) + float(by_k.get("us_stocks", 0.0)) + float(by_k.get("gold", 0.0)),
        "locked": float(nw.get("cd_egp", 0.0)),
    }
    gross = sum(values.values())
    return [
        {
            "key": k,
            "label": label,
            "color": color,
            "help": help_,
            "value": values[k],
            "pct": (values[k] / gross * 100.0) if gross > 0 else 0.0,
        }
        for k, label, color, help_ in LIQUIDITY_TIERS
    ]


def emergency_fund_months(nw: dict, monthly_expenses: float) -> Optional[float]:
    """Months of expenses covered by cash + mutual funds (``None`` if expenses aren't set)."""
    if monthly_expenses <= 0:
        return None
    tiers = {t["key"]: t["value"] for t in liquidity_breakdown(nw)}
    return (tiers["cash"] + tiers["funds"]) / monthly_expenses


# --- 9. passive income -----------------------------------------------------------------------------
def trailing_dividends(dividends: Iterable[object], today: date, days: int = 365) -> float:
    """Sum of dividends received in the last ``days`` days (objects/dicts with ``amount`` and ``date``)."""
    total = 0.0
    for d in dividends:
        amount = d["amount"] if isinstance(d, Mapping) else getattr(d, "amount", 0.0)
        when = _parse_day(d["date"] if isinstance(d, Mapping) else getattr(d, "date", ""))
        if when is not None and 0 <= (today - when).days <= days:
            total += float(amount or 0.0)
    return total


def passive_income(nw: dict, dividends_12m: float = 0.0, monthly_expenses: float = 0.0) -> dict:
    """Monthly passive income (CD/cash interest, fund yield, trailing dividends) net of debt interest."""
    cd = float(nw.get("annual_cd_income", 0.0)) / 12.0
    funds = float(nw.get("annual_fund_income", 0.0)) / 12.0
    divs = float(dividends_12m) / 12.0
    interest = float(nw.get("annual_interest_cost", 0.0)) / 12.0
    gross = cd + funds + divs
    net = gross - interest
    return {
        "cd_monthly": cd,
        "fund_monthly": funds,
        "dividend_monthly": divs,
        "interest_monthly": interest,
        "gross_monthly": gross,
        "net_monthly": net,
        "expense_coverage_pct": (net / monthly_expenses * 100.0) if monthly_expenses > 0 else None,
    }


# --- 7. concentration ------------------------------------------------------------------------------
def us_ticker(name: str) -> str:
    """Ticker from a US stock item label: 'PCOR (ESPP)' -> 'PCOR'."""
    token = (name or "").strip().split(" ")[0] if (name or "").strip() else ""
    return "".join(ch for ch in token if ch.isalnum() or ch in ".-").upper()


def holdings_concentration(
    positions: Iterable[Position],
    fund_symbols: Iterable[str],
    items: Iterable[NetWorthItem],
    nw: dict,
    usd_egp_rate: float = 0.0,
) -> list[dict]:
    """Single-company stock holdings (EGX stocks and US/employer stock) as a % of gross assets.

    Funds (stock funds, mutual funds), gold, cash and CDs are diversified or not single-company risk,
    so they are excluded. Several US stock items with the same ticker (e.g. 'PCOR' and 'PCOR (ESPP)')
    are combined. Sorted largest first.
    """
    funds = set(fund_symbols)
    gross = float(nw.get("gross_assets", nw.get("total", 0.0)))
    holdings: dict[str, dict] = {}
    for p in positions:
        if p.quantity <= 0 or p.symbol in funds:
            continue
        val = p.market_value if p.market_value is not None else p.cost_basis_remaining
        holdings.setdefault(p.symbol, {"name": p.symbol, "kind": "EGX stock", "value": 0.0})["value"] += val
    for it in items:
        if it.category != CAT_US_STOCK or it.quantity <= 0:
            continue
        key = us_ticker(it.name) or it.name
        holdings.setdefault(key, {"name": key, "kind": "US / employer stock", "value": 0.0})["value"] += item_value_egp(
            it, usd_egp_rate=usd_egp_rate
        )
    rows = [dict(h, pct=(h["value"] / gross * 100.0) if gross > 0 else 0.0) for h in holdings.values()]
    rows.sort(key=lambda r: -r["value"])
    return rows


# --- 6. targets & drift ----------------------------------------------------------------------------
def allocation_drift(nw: dict, targets: Mapping[str, float]) -> list[dict]:
    """Current vs target allocation per asset class (shares of gross assets).

    ``targets`` maps allocation keys to target %; keys without a target are reported with
    ``target_pct=None``. ``move_egp`` is how much to add (+) or take out (−) to hit the target.
    """
    gross = float(nw.get("gross_assets", nw.get("total", 0.0)))
    rows = []
    for k in ALLOCATION_KEYS:
        cur_v = float(nw["by_key"].get(k, 0.0))
        cur_pct = (cur_v / gross * 100.0) if gross > 0 else 0.0
        tgt = targets.get(k)
        row = {"key": k, "label": ALLOCATION_LABELS[k], "value": cur_v, "current_pct": cur_pct,
               "target_pct": None, "drift_pct": None, "move_egp": None}
        if tgt is not None:
            row["target_pct"] = float(tgt)
            row["drift_pct"] = cur_pct - float(tgt)
            row["move_egp"] = float(tgt) / 100.0 * gross - cur_v
        rows.append(row)
    return rows


def usd_target_gap(nw: dict, target_pct: float) -> dict:
    """How far net USD exposure is from a target share of net worth (``move_egp`` > 0 = buy USD)."""
    total = float(nw.get("total", 0.0))
    cur = float(nw.get("usd_total_egp", 0.0))
    cur_pct = (cur / total * 100.0) if total > 0 else 0.0
    move = target_pct / 100.0 * total - cur
    rate = float(nw.get("usd_egp_rate", 0.0))
    return {
        "current_pct": cur_pct,
        "target_pct": float(target_pct),
        "drift_pct": cur_pct - float(target_pct),
        "move_egp": move,
        "move_usd": (move / rate) if rate > 0 else None,
    }


# --- 4 & 10. change attribution and real growth ---------------------------------------------------
def egx_net_invested(txns: Iterable[Transaction], start_iso: str, end_iso: str) -> float:
    """Net cash put into EGX (buys incl. fees − sell proceeds net of fees) with start < date <= end."""
    total = 0.0
    for t in txns:
        d = str(t.date)[:10]
        if not (start_iso < d <= end_iso):
            continue
        gross = t.quantity * t.price
        total += (gross + t.fee) if t.side == "buy" else -(gross - t.fee)
    return total


def real_change_pct(nominal_pct: float, inflation_annual_pct: float, days: int) -> Optional[float]:
    """Inflation-adjusted % change over ``days`` given an annual inflation rate (compounded)."""
    if inflation_annual_pct <= 0 or days <= 0:
        return None
    infl_factor = (1.0 + inflation_annual_pct / 100.0) ** (days / 365.0)
    return ((1.0 + nominal_pct / 100.0) / infl_factor - 1.0) * 100.0


def change_breakdown(prev: Mapping[str, float], cur: Mapping[str, float], egx_invested: float = 0.0) -> dict:
    """Explain the change in net worth between two snapshot rows.

    Splits the total change into:
      * ``usd_rate`` — revaluation of the dollars held at ``prev`` because the USD rate moved
      * ``usd_holdings`` — change in the *dollar amount* held (US stock price, new USD, interest), at today's rate
      * ``gold_price`` — revaluation of the gold held at ``prev`` because the gold price moved
      * ``egx_invested`` — net new money put into (or taken out of) EGX stocks
      * ``egx_market`` — EGX market movement (change in EGX value not explained by money in/out)
      * ``other`` — everything else: EGP funds/CDs/cash balance changes, gold bought/sold, debt changes

    Rate-based splits need both snapshots to carry ``usd_egp_rate`` / ``gold_24k_price``; when they
    don't, that effect is reported as ``None`` and folded into ``other``.
    """
    g = lambda row, k: float(row.get(k) or 0.0)  # noqa: E731
    d_total = g(cur, "total_egp") - g(prev, "total_egp")

    r0, r1 = g(prev, "usd_egp_rate"), g(cur, "usd_egp_rate")
    usd_rate = usd_holdings = None
    if r0 > 0 and r1 > 0:
        u0 = g(prev, "usd_total_usd") or (g(prev, "usd_total_egp") / r0)
        u1 = g(cur, "usd_total_usd") or (g(cur, "usd_total_egp") / r1)
        usd_rate = u0 * (r1 - r0)
        usd_holdings = (u1 - u0) * r1

    p0, p1 = g(prev, "gold_24k_price"), g(cur, "gold_24k_price")
    gold_price = g(prev, "gold_egp") * (p1 / p0 - 1.0) if p0 > 0 and p1 > 0 else None

    egx_market = (g(cur, "stocks_egp") - g(prev, "stocks_egp")) - egx_invested
    explained = (usd_rate or 0.0) + (usd_holdings or 0.0) + (gold_price or 0.0) + egx_invested + egx_market
    by_category = [
        {"key": k, "label": ALLOCATION_LABELS[k], "prev": g(prev, f"{k}_egp"), "cur": g(cur, f"{k}_egp"),
         "change": g(cur, f"{k}_egp") - g(prev, f"{k}_egp")}
        for k in ALLOCATION_KEYS
    ]
    by_category.append(
        {"key": "liabilities", "label": "Debts (owed)", "prev": g(prev, "liabilities_egp"),
         "cur": g(cur, "liabilities_egp"), "change": g(cur, "liabilities_egp") - g(prev, "liabilities_egp")}
    )
    return {
        "total_change": d_total,
        "total_change_pct": (d_total / g(prev, "total_egp") * 100.0) if g(prev, "total_egp") > 0 else None,
        "usd_rate": usd_rate,
        "usd_holdings": usd_holdings,
        "gold_price": gold_price,
        "egx_invested": egx_invested,
        "egx_market": egx_market,
        "other": d_total - explained,
        "by_category": by_category,
    }
