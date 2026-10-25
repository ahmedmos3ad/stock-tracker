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
    usd_fx_slice_egp = 0.0

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
                usd_fx_slice_egp += v
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
        # USD-denominated EGP value inside each chart slice (USD cash in the 'cash' category lives in cd_cash)
        "usd_by_key": {
            "stocks": 0.0,
            "us_stocks": usd_stock_egp,
            "funds": usd_fund_egp,
            "gold": 0.0,
            "fx": usd_fx_slice_egp,
            "cd_cash": usd_total_egp - usd_stock_egp - usd_fund_egp - usd_fx_slice_egp,
        },
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


def snapshot_from_nw(date_iso: str, nw: dict) -> dict:
    """Convert a ``compute_net_worth`` result into a snapshot row dict keyed by ``date``."""
    by_k = nw["by_key"]
    return {
        "date": str(date_iso)[:10],
        "total_egp": float(nw["total"]),
        "stocks_egp": float(by_k.get("stocks", 0.0)),
        "us_stocks_egp": float(by_k.get("us_stocks", 0.0)),
        "funds_egp": float(by_k.get("funds", 0.0)),
        "gold_egp": float(by_k.get("gold", 0.0)),
        "fx_egp": float(by_k.get("fx", 0.0)),
        "cd_cash_egp": float(by_k.get("cd_cash", 0.0)),
        "usd_total_egp": float(nw.get("usd_total_egp", 0.0)),
    }


SNAPSHOT_SLICE_KEYS = ("stocks", "us_stocks", "funds", "gold", "fx", "cd_cash")


def snapshot_usd_exposure(edited: dict, base: dict, live_nw: dict) -> float:
    """USD exposure (EGP) for a manually edited snapshot, kept consistent with its slice values.

    ``base`` is the snapshot the form was pre-filled with (saved row or live values) and ``edited``
    holds the values being saved (``<key>_egp`` fields). Starting from ``base['usd_total_egp']``, each
    slice's change is attributed to USD in proportion to that slice's live USD share, so editing e.g.
    the US Stock slice moves USD exposure with it instead of leaving a stale pre-filled number.
    """
    by_k = live_nw.get("by_key", {})
    usd_by_k = live_nw.get("usd_by_key", {})
    usd = float(base.get("usd_total_egp", 0.0))
    for k in SNAPSHOT_SLICE_KEYS:
        live_v = float(by_k.get(k, 0.0))
        frac = (float(usd_by_k.get(k, 0.0)) / live_v) if live_v > 0 else 0.0
        delta = float(edited.get(f"{k}_egp", 0.0)) - float(base.get(f"{k}_egp", 0.0))
        usd += delta * min(max(frac, 0.0), 1.0)
    total = sum(float(edited.get(f"{k}_egp", 0.0)) for k in SNAPSHOT_SLICE_KEYS)
    return round(min(max(usd, 0.0), total), 2)


def build_snapshot_series(
    saved_snapshots: Iterable[dict],
    today_iso: str,
    live_nw: dict,
) -> list[dict]:
    """Merge persisted snapshots with today's live Net Worth so the chart always ends at today."""
    by_date: dict[str, dict] = {}
    for s in saved_snapshots:
        d = str(s["date"])[:10]
        by_date[d] = {
            "date": d,
            "total_egp": round(float(s["total_egp"]), 2),
            "stocks_egp": round(float(s.get("stocks_egp", 0.0)), 2),
            "us_stocks_egp": round(float(s.get("us_stocks_egp", 0.0)), 2),
            "funds_egp": round(float(s.get("funds_egp", 0.0)), 2),
            "gold_egp": round(float(s.get("gold_egp", 0.0)), 2),
            "fx_egp": round(float(s.get("fx_egp", 0.0)), 2),
            "cd_cash_egp": round(float(s.get("cd_cash_egp", 0.0)), 2),
            "usd_total_egp": round(float(s.get("usd_total_egp", 0.0)), 2),
        }
    today_row = snapshot_from_nw(today_iso, live_nw)
    by_date[today_row["date"]] = {
        k: (round(v, 2) if isinstance(v, float) else v) for k, v in today_row.items()
    }
    return [by_date[d] for d in sorted(by_date)]


NW_CHART_HEIGHT = 490


def nw_chart_html(series: list[dict], ccy: str = "EGP", default_range: str = "1M") -> str:
    """Interactive HTML/SVG card for Net Worth over time (Total line + Stacked Asset Breakdown)."""
    import json

    return (
        _NW_CHART_TEMPLATE.replace("__DATA__", json.dumps(series))
        .replace("__CCY__", json.dumps(ccy))
        .replace("__RANGE__", json.dumps(default_range))
    )


_NW_CHART_TEMPLATE = r"""
<style>
  html,body{margin:0;background:transparent;color:#e6e6e6;font-family:"Source Sans Pro",system-ui,sans-serif}
  .card{background:#1c1f26;border:1px solid #2a2e37;border-radius:14px;padding:16px 18px;user-select:none}
  .top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
  .title{font-size:1.05rem;font-weight:700}
  .seg button,.pills button{background:transparent;border:1px solid #2b2f36;color:#8a8f98;cursor:pointer;
    font:inherit;font-size:.86rem;padding:5px 11px}
  .seg button{border-radius:4px}
  .seg button.on{background:#2a2d33;color:#fff;border-color:#4a4f5c}
  .head{display:flex;justify-content:space-between;align-items:flex-end;margin-top:10px;min-height:54px;flex-wrap:wrap;gap:12px}
  .lbl{color:#8a8f98;font-size:.82rem;margin-bottom:2px}
  .big{font-size:1.75rem;line-height:1.15;font-weight:600;color:#fff}
  .delta{font-size:.95rem;font-weight:600;margin-left:10px}
  .up{color:#2ecc71}.down{color:#e74c3c}.flat{color:#9aa0a6}
  #plot{position:relative;margin-top:10px}
  svg{display:block;width:100%;overflow:visible}
  #tip{position:absolute;pointer-events:none;background:#252932;border:1px solid #3a3f4b;border-radius:8px;
    padding:10px 14px;font-size:.82rem;display:none;white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,.55);z-index:10}
  #tip .t{color:#9aa0a6;margin-bottom:6px;font-weight:600}
  #tip .row{display:flex;gap:14px;justify-content:space-between;padding:1px 0}
  #tip b{color:#fff}
  .pills{display:flex;justify-content:center;gap:8px;margin-top:14px}
  .pills button{border:1px solid transparent;color:#9aa0a6;font-weight:600;border-radius:4px;padding:5px 11px}
  .pills button.on{background:#2a2d33;color:#fff}
</style>
<div class="card">
  <div class="top">
    <div class="title">📈 Net Worth Over Time</div>
    <div class="seg">
      <button id="vTotal" class="on">Total Net Worth</button>
      <button id="vStack">Stacked Breakdown</button>
    </div>
  </div>
  <div class="head">
    <div>
      <div class="lbl" id="lbl">Net Worth</div>
      <div><span class="big" id="big"></span><span class="delta" id="delta"></span></div>
    </div>
    <div class="lbl" id="subRight"></div>
  </div>
  <div id="plot"><svg id="svg"></svg><div id="tip"></div></div>
  <div class="pills" id="pills"></div>
</div>
<script>
const D = __DATA__.map(p => ({...p, t: Date.parse(p.date + "T00:00:00Z")}));
const CCY = __CCY__, GREEN = "#2ecc71", RED = "#e74c3c", BG = "#1c1f26";
const SLICES = [
  {k: "cd_cash_egp", label: "CDs & Cash", color: "#1abc9c"},
  {k: "fx_egp", label: "FX Cash", color: "#3498db"},
  {k: "gold_egp", label: "Gold", color: "#f1c40f"},
  {k: "funds_egp", label: "Mutual Funds", color: "#8c8cff"},
  {k: "us_stocks_egp", label: "US Stock", color: "#e67e22"},
  {k: "stocks_egp", label: "EGX Equities", color: "#2ecc71"}
];
const RANGES = ["1M", "3M", "6M", "1Y", "YTD", "All"];
let view = "total", range = __RANGE__, hover = null, pts = [];
const H = 265, M = {l: 52, r: 10, t: 10, b: 22};
const $ = id => document.getElementById(id);

function fmtDate(t) {
  return new Date(t).toLocaleDateString("en-GB", {day: "2-digit", month: "short", year: "numeric", timeZone: "UTC"});
}
function money(n) { return n.toLocaleString("en-US", {minimumFractionDigits: 0, maximumFractionDigits: 0}) + " " + CCY; }
function kfmt(n) {
  const a = Math.abs(n);
  return a >= 1e6 ? (n / 1e6).toFixed(2) + "M" : a >= 1e3 ? Math.round(n / 1e3) + "K" : String(Math.round(n));
}
function niceTicks(lo, hi, n) {
  if (hi <= lo) return [lo];
  const raw = (hi - lo) / n, mag = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / mag;
  const step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * mag, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}
function sliceRange() {
  if (!D.length) return [];
  const last = D[D.length - 1], day = 86400000;
  let cut = 0;
  if (range === "1M") cut = last.t - 31 * day;
  else if (range === "3M") cut = last.t - 92 * day;
  else if (range === "6M") cut = last.t - 183 * day;
  else if (range === "1Y") cut = last.t - 365 * day;
  else if (range === "YTD") cut = Date.UTC(new Date(last.t).getUTCFullYear(), 0, 1);
  let out = D.filter(p => p.t >= cut);
  if (out.length < 2 && D.length >= 2) out = D.slice(-2);
  return out;
}
function smoothPath(xy) {
  const n = xy.length;
  if (n === 0) return "";
  if (n === 1) return "M" + xy[0][0] + "," + xy[0][1];
  const dx = [], m = [], t = new Array(n);
  for (let i = 0; i < n - 1; i++) { dx.push(xy[i + 1][0] - xy[i][0]); m.push((xy[i + 1][1] - xy[i][1]) / (dx[i] || 1)); }
  t[0] = m[0]; t[n - 1] = m[n - 2];
  for (let i = 1; i < n - 1; i++) t[i] = m[i - 1] * m[i] <= 0 ? 0 : (m[i - 1] + m[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (m[i] === 0) { t[i] = 0; t[i + 1] = 0; continue; }
    const a = t[i] / m[i], b = t[i + 1] / m[i], s = a * a + b * b;
    if (s > 9) { const k = 3 / Math.sqrt(s); t[i] = k * a * m[i]; t[i + 1] = k * b * m[i]; }
  }
  let d = "M" + xy[0][0] + "," + xy[0][1];
  for (let i = 0; i < n - 1; i++) {
    const h = dx[i] / 3;
    d += "C" + (xy[i][0] + h) + "," + (xy[i][1] + t[i] * h) + " " + (xy[i + 1][0] - h) + "," + (xy[i + 1][1] - t[i + 1] * h) +
         " " + xy[i + 1][0] + "," + xy[i + 1][1];
  }
  return d;
}
function setHeader() {
  if (!pts.length) return;
  const i = hover === null ? pts.length - 1 : hover, p = pts[i], first = pts[0];
  const diff = p.total_egp - first.total_egp;
  const pct = first.total_egp > 0 ? (diff / first.total_egp) * 100 : 0;
  const sign = diff > 0 ? "+" : diff < 0 ? "\u2212" : "";
  const cls = diff > 0 ? "up" : diff < 0 ? "down" : "flat";
  $("lbl").textContent = "Net Worth on " + fmtDate(p.t);
  $("big").textContent = money(p.total_egp);
  if (pts.length >= 2 && i > 0) {
    $("delta").className = "delta " + cls;
    $("delta").textContent = `${sign}${money(Math.abs(diff))} (${sign}${Math.abs(pct).toFixed(2)}%) vs ${fmtDate(first.t)}`;
  } else {
    $("delta").textContent = "";
  }
  const usdPct = p.total_egp > 0 ? (p.usd_total_egp / p.total_egp * 100).toFixed(1) : "0.0";
  $("subRight").textContent = `${pts.length} snapshot(s) · USD share: ${usdPct}%`;
}
function render() {
  pts = sliceRange();
  const W = $("plot").clientWidth || 700, pw = W - M.l - M.r, ph = H - M.t - M.b;
  let lo = 0, hi = 1;
  if (view === "total") {
    lo = Math.min(...pts.map(p => p.total_egp));
    hi = Math.max(...pts.map(p => p.total_egp));
    const pad = (hi - lo) * 0.18 || Math.abs(hi) * 0.08 || 1000;
    lo = Math.max(0, lo - pad); hi += pad;
  } else {
    lo = 0;
    hi = Math.max(...pts.map(p => p.total_egp)) * 1.08 || 1000;
  }
  const t0 = pts[0].t, t1 = pts.length > 1 ? pts[pts.length - 1].t : t0 + 1;
  const X = t => pts.length === 1 ? M.l + pw / 2 : M.l + ((t - t0) / (t1 - t0)) * pw;
  const Y = v => M.t + (1 - (v - lo) / (hi - lo || 1)) * ph;

  let g = "";
  for (const v of niceTicks(lo, hi, 4)) {
    const y = Y(v);
    g += `<line x1="${M.l}" x2="${W - M.r}" y1="${y}" y2="${y}" stroke="#262a31"/>` +
         `<text x="${M.l - 8}" y="${y + 4}" text-anchor="end" fill="#8a8f98" font-size="11">${kfmt(v)}</text>`;
  }
  g += `<line x1="${M.l}" x2="${W - M.r}" y1="${H - M.b}" y2="${H - M.b}" stroke="#3a3f48"/>`;

  if (view === "total") {
    const color = pts[pts.length - 1].total_egp >= pts[0].total_egp ? GREEN : RED;
    g += `<defs><linearGradient id="nwg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".28"/>` +
         `<stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient></defs>`;
    if (pts.length > 1) {
      const xy = pts.map(p => [X(p.t), Y(p.total_egp)]), line = smoothPath(xy);
      g += `<path d="${line} L${xy[xy.length - 1][0]},${H - M.b} L${xy[0][0]},${H - M.b} Z" fill="url(#nwg)"/>` +
           `<path d="${line}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round"/>`;
    }
    for (const p of pts) {
      g += `<circle cx="${X(p.t)}" cy="${Y(p.total_egp)}" r="4" fill="${color}" stroke="#1c1f26" stroke-width="1.5"/>`;
    }
  } else {
    const cum = pts.map(() => 0);
    for (const s of SLICES) {
      const prev = [...cum];
      for (let i = 0; i < pts.length; i++) cum[i] += Math.max(0, pts[i][s.k] || 0);
      if (pts.length > 1) {
        const topPts = pts.map((p, i) => `${X(p.t)},${Y(cum[i])}`).join(" L");
        const botPts = pts.map((p, i) => `${X(p.t)},${Y(prev[i])}`).reverse().join(" L");
        g += `<path d="M${topPts} L${botPts} Z" fill="${s.color}" fill-opacity=".45" stroke="${s.color}" stroke-width="1.4"/>`;
      } else {
        const x = X(pts[0].t);
        g += `<line x1="${x - 30}" x2="${x + 30}" y1="${Y(cum[0])}" y2="${Y(cum[0])}" stroke="${s.color}" stroke-width="4"/>`;
      }
    }
  }

  if (hover !== null && pts[hover]) {
    const x = X(pts[hover].t);
    g += `<line x1="${x}" x2="${x}" y1="${M.t}" y2="${H - M.b}" stroke="#fff" stroke-width="1.5" stroke-dasharray="3 3"/>`;
  }
  g += `<rect id="hit" x="${M.l}" y="0" width="${pw}" height="${H}" fill="transparent"/>`;
  const svg = $("svg"); svg.setAttribute("height", H); svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.innerHTML = g;

  const hit = $("hit");
  const move = e => {
    const r = svg.getBoundingClientRect(), cx = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(X(pts[i].t) - cx) < Math.abs(X(pts[best].t) - cx)) best = i;
    if (best !== hover) { hover = best; render(); showTip(X(pts[best].t), Y(pts[best].total_egp)); }
  };
  hit.onmousemove = move; hit.ontouchmove = move;
  hit.onmouseleave = hit.ontouchend = () => { hover = null; $("tip").style.display = "none"; render(); };
  setHeader();
  if (hover !== null && pts[hover]) showTip(X(pts[hover].t), Y(pts[hover].total_egp));
}
function showTip(x, y) {
  const p = pts[hover], tip = $("tip");
  const row = (l, v, c) => `<div class="row"><span style="color:${c || '#9aa0a6'}">${l}</span><b>${money(v)}</b></div>`;
  let html = `<div class="t">${fmtDate(p.t)}</div>` + row("Total Net Worth", p.total_egp, "#fff");
  for (const s of [...SLICES].reverse()) {
    if ((p[s.k] || 0) > 0) html += row(s.label, p[s.k], s.color);
  }
  tip.innerHTML = html;
  tip.style.display = "block";
  const w = tip.offsetWidth, plotW = $("plot").clientWidth;
  tip.style.left = Math.max(8, (x + 14 + w > plotW ? x - 14 - w : x + 14)) + "px";
  tip.style.top = Math.max(4, Math.min(y, H - tip.offsetHeight - 4)) + "px";
}
$("vTotal").onclick = () => { view = "total"; hover = null; $("vTotal").classList.add("on"); $("vStack").classList.remove("on"); render(); };
$("vStack").onclick = () => { view = "stack"; hover = null; $("vStack").classList.add("on"); $("vTotal").classList.remove("on"); render(); };
RANGES.forEach(r => {
  const b = document.createElement("button"); b.textContent = r; b.dataset.r = r;
  b.onclick = () => { range = r; hover = null; document.querySelectorAll("#pills button")
    .forEach(x => x.classList.toggle("on", x.dataset.r === r)); render(); };
  if (r === range) b.classList.add("on");
  $("pills").appendChild(b);
});
window.addEventListener("resize", render);
render();
</script>
"""
