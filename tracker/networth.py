"""Net-worth calculations across EGX equities, US/employer stock, mutual funds, gold, FX, and CDs/cash.

Pure functions and data structures, free of Streamlit so they can be unit-tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .portfolio import Position

CAT_GOLD = "gold"
CAT_FX = "fx"
CAT_CD = "cd"
CAT_CASH = "cash"
CAT_FUND = "fund"
CAT_US_STOCK = "us_stock"
VALID_CATEGORIES = (CAT_GOLD, CAT_FX, CAT_CD, CAT_CASH, CAT_FUND, CAT_US_STOCK)

# Standard Egyptian gold items: label -> (karat, weight_in_grams)
GOLD_PRESETS: dict[str, tuple[int, float]] = {
    "Gold Pound (8g · 21K)": (21, 8.0),
    "Half Pound (4g · 21K)": (21, 4.0),
    "Quarter Pound (2g · 21K)": (21, 2.0),
    "1g Bar (24K)": (24, 1.0),
    "2.5g Bar (24K)": (24, 2.5),
    "5g Bar (24K)": (24, 5.0),
    "10g Bar (24K)": (24, 10.0),
    "20g Bar (24K)": (24, 20.0),
    "31.1g Ounce Bar (24K)": (24, 31.1),
    "50g Bar (24K)": (24, 50.0),
    "100g Bar (24K)": (24, 100.0),
}

COMMON_CURRENCIES = ("USD", "EUR", "SAR", "GBP", "AED", "KWD", "CHF")
CD_CURRENCIES = ("EGP", "USD", "EUR", "SAR", "GBP", "AED")


@dataclass(frozen=True)
class NetWorthItem:
    id: int
    category: str           # 'gold', 'fx', 'cd', 'cash', 'fund', 'us_stock'
    name: str               # e.g. 'PCOR', 'Gold Pound (8g · 21K)', 'USD', 'NBE USD CD'
    quantity: float         # shares (us_stock), pieces (gold), units/balance (fund), amount (fx/cd/cash)
    unit_value: float = 0.0   # $/share (us_stock), cashback/g (gold), NAV if >0 (fund), interest % (cd/cash)
    karat: int = 24         # 21 or 24 (used for gold)
    grams: float = 0.0      # weight per piece in grams (gold) or expected annual rate % (fund)
    currency: str = "EGP"   # 'EGP', 'USD', 'EUR', ...
    fx_rate: float = 1.0    # EGP per 1 unit of `currency`
    notes: str = ""


def effective_gold_prices(price_24k: float, price_21k: float) -> tuple[float, float]:
    """Return ``(effective_24k, effective_21k)`` in EGP/gram.

    If only one karat price is entered (> 0) and the other is 0, the missing one is derived
    automatically via the exact karat ratio (21 / 24 = 7 / 8).
    """
    p24 = max(float(price_24k or 0.0), 0.0)
    p21 = max(float(price_21k or 0.0), 0.0)
    if p24 > 0 and p21 == 0:
        p21 = round(p24 * 21.0 / 24.0, 2)
    elif p21 > 0 and p24 == 0:
        p24 = round(p21 * 24.0 / 21.0, 2)
    return p24, p21


def item_currency(item: NetWorthItem) -> str:
    """Resolved ISO currency code for an item ('EGP', 'USD', ...)."""
    if item.category == CAT_GOLD:
        return "EGP"
    ccy = (item.currency or "").strip().upper()
    if item.category == CAT_US_STOCK and (not ccy or ccy == "EGP"):
        return "USD"
    if item.category == CAT_FX and (not ccy or ccy == "EGP"):
        return (item.name or "USD").strip().upper()
    return ccy or "EGP"


def effective_fx_rate(item: NetWorthItem, usd_egp_rate: float = 0.0) -> float:
    """Return the EGP exchange rate per 1 unit of ``item``'s currency."""
    ccy = item_currency(item)
    if ccy == "EGP":
        return 1.0
    stored_rate = float(item.fx_rate or 0.0)
    # Backward-compatible fallback: FX items that stored their rate in `unit_value` with fx_rate=1.0
    if item.category == CAT_FX and stored_rate <= 1.0 and item.unit_value > 0:
        stored_rate = float(item.unit_value)
    if ccy == "USD" and float(usd_egp_rate or 0.0) > 0:
        return float(usd_egp_rate)
    return max(stored_rate, 0.0)


def item_native_value(item: NetWorthItem) -> float:
    """Value of a non-gold item in its native currency (before FX conversion)."""
    if item.quantity <= 0:
        return 0.0
    if item.category == CAT_US_STOCK:
        return item.quantity * max(item.unit_value, 0.0)
    if item.category == CAT_FUND:
        # unit_value > 0 means `quantity` is units and `unit_value` is NAV per unit;
        # unit_value == 0 means `quantity` is already the total balance in native currency.
        return item.quantity * item.unit_value if item.unit_value > 0 else item.quantity
    return item.quantity


def item_value_egp(
    item: NetWorthItem,
    price_24k: float = 0.0,
    price_21k: float = 0.0,
    usd_egp_rate: float = 0.0,
) -> float:
    """Current EGP value of a single non-stock net-worth item."""
    if item.quantity <= 0:
        return 0.0
    if item.category == CAT_GOLD:
        p24, p21 = effective_gold_prices(price_24k, price_21k)
        gram_price = p21 if item.karat == 21 else p24
        return item.quantity * item.grams * (gram_price + max(item.unit_value, 0.0))
    return item_native_value(item) * effective_fx_rate(item, usd_egp_rate)


def item_annual_income_native(item: NetWorthItem) -> float:
    """Annual expected income/yield in the item's native currency for CDs, cash, and mutual funds."""
    if item.category in (CAT_CD, CAT_CASH) and item.quantity > 0 and item.unit_value > 0:
        return item.quantity * (item.unit_value / 100.0)
    if item.category == CAT_FUND and item.quantity > 0 and item.grams > 0:
        return item_native_value(item) * (item.grams / 100.0)
    return 0.0


def item_annual_income_egp(item: NetWorthItem, usd_egp_rate: float = 0.0) -> float:
    """Annual expected income/yield converted to EGP for CDs, cash, and mutual funds."""
    return item_annual_income_native(item) * effective_fx_rate(item, usd_egp_rate)


def portfolio_split_egp(
    positions: Iterable[Position],
    fund_symbols: Iterable[str],
) -> tuple[float, float]:
    """Return ``(stocks_egp, stock_funds_egp)`` for all open positions in the main tracker.

    Uses market value when priced, falling back to remaining cost basis when unpriced
    (consistent with the Allocation and Performance tabs).
    """
    funds = set(fund_symbols)
    stocks_egp = 0.0
    stock_funds_egp = 0.0
    for p in positions:
        if p.quantity <= 0:
            continue
        val = p.market_value if p.market_value is not None else p.cost_basis_remaining
        if p.symbol in funds:
            stock_funds_egp += val
        else:
            stocks_egp += val
    return stocks_egp, stock_funds_egp


def compute_net_worth(
    positions: Iterable[Position],
    fund_symbols: Iterable[str],
    items: Iterable[NetWorthItem],
    price_24k: float = 0.0,
    price_21k: float = 0.0,
    usd_egp_rate: float = 0.0,
) -> dict:
    """Aggregate Net Worth in EGP across all asset classes plus currency/USD exposure."""
    stocks_only_val, stock_funds_val = portfolio_split_egp(positions, fund_symbols)
    equities_val = stocks_only_val + stock_funds_val
    us_stocks_val = 0.0
    mutual_funds_val = 0.0
    gold_val = 0.0
    fx_val = 0.0
    cd_cash_val = 0.0
    grams_24k = 0.0
    grams_21k = 0.0
    annual_income = 0.0
    annual_fund_income = 0.0

    usd_cash_usd = 0.0
    usd_cd_usd = 0.0
    usd_fund_usd = 0.0
    usd_stock_usd = 0.0
    usd_cash_egp = 0.0
    usd_cd_egp = 0.0
    usd_fund_egp = 0.0
    usd_stock_egp = 0.0
    other_fx_egp = 0.0
    egp_non_equity_egp = 0.0

    for it in items:
        v = item_value_egp(it, price_24k, price_21k, usd_egp_rate)
        native_v = item_native_value(it)
        ccy = item_currency(it)
        if it.category == CAT_GOLD:
            gold_val += v
            if it.karat == 21:
                grams_21k += it.quantity * it.grams
            else:
                grams_24k += it.quantity * it.grams
        elif it.category == CAT_US_STOCK:
            us_stocks_val += v
            if ccy == "USD":
                usd_stock_usd += native_v
                usd_stock_egp += v
            elif ccy != "EGP":
                other_fx_egp += v
            else:
                egp_non_equity_egp += v
        elif it.category == CAT_FUND:
            mutual_funds_val += v
            annual_fund_income += item_annual_income_egp(it, usd_egp_rate)
            if ccy == "USD":
                usd_fund_usd += native_v
                usd_fund_egp += v
            elif ccy != "EGP":
                other_fx_egp += v
            else:
                egp_non_equity_egp += v
        elif it.category == CAT_FX:
            fx_val += v
            if ccy == "USD":
                usd_cash_usd += native_v
                usd_cash_egp += v
            elif ccy != "EGP":
                other_fx_egp += v
            else:
                egp_non_equity_egp += v
        elif it.category in (CAT_CD, CAT_CASH):
            cd_cash_val += v
            annual_income += item_annual_income_egp(it, usd_egp_rate)
            if ccy == "USD":
                if it.category == CAT_CD:
                    usd_cd_usd += native_v
                    usd_cd_egp += v
                else:
                    usd_cash_usd += native_v
                    usd_cash_egp += v
            elif ccy != "EGP":
                other_fx_egp += v
            else:
                egp_non_equity_egp += v

    raw_slices = [
        ("stocks", "EGX Equities (Stocks & Funds)", equities_val, "#2ecc71"),
        ("us_stocks", "US / Employer Stock", us_stocks_val, "#e67e22"),
        ("funds", "Mutual Funds", mutual_funds_val, "#8c8cff"),
        ("gold", "Gold", gold_val, "#f1c40f"),
        ("fx", "Foreign Currency (Cash)", fx_val, "#3498db"),
        ("cd_cash", "CDs & Cash", cd_cash_val, "#1abc9c"),
    ]
    total = sum(v for _, _, v, _ in raw_slices)
    denom = total if total > 0 else 1.0
    slices = [
        {
            "key": k,
            "label": label,
            "value": val,
            "pct": (val / denom * 100.0) if total > 0 else 0.0,
            "color": color,
        }
        for k, label, val, color in raw_slices
    ]

    usd_total_usd = usd_cash_usd + usd_cd_usd + usd_fund_usd + usd_stock_usd
    usd_total_egp = usd_cash_egp + usd_cd_egp + usd_fund_egp + usd_stock_egp
    egp_exposure_egp = equities_val + egp_non_equity_egp

    return {
        "total": total,
        "slices": slices,
        "by_key": {s["key"]: s["value"] for s in slices},
        "stocks_only_egp": stocks_only_val,
        "stock_funds_egp": stock_funds_val,
        "gold_grams_24k": grams_24k,
        "gold_grams_21k": grams_21k,
        "gold_total_grams": grams_24k + grams_21k,
        "annual_cd_income": annual_income,
        "annual_fund_income": annual_fund_income,
        "usd_cash_usd": usd_cash_usd,
        "usd_cd_usd": usd_cd_usd,
        "usd_fund_usd": usd_fund_usd,
        "usd_stock_usd": usd_stock_usd,
        "usd_total_usd": usd_total_usd,
        "usd_cash_egp": usd_cash_egp,
        "usd_cd_egp": usd_cd_egp,
        "usd_fund_egp": usd_fund_egp,
        "usd_stock_egp": usd_stock_egp,
        "usd_total_egp": usd_total_egp,
        "usd_pct": (usd_total_egp / denom * 100.0) if total > 0 else 0.0,
        "other_fx_egp": other_fx_egp,
        "other_fx_pct": (other_fx_egp / denom * 100.0) if total > 0 else 0.0,
        "egp_exposure_egp": egp_exposure_egp,
        "egp_pct": (egp_exposure_egp / denom * 100.0) if total > 0 else 0.0,
        "gold_pct": (gold_val / denom * 100.0) if total > 0 else 0.0,
    }
