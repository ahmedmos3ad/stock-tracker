"""Streamlit dashboard for tracking EGX/THNDR stock positions.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import base64
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dotenv import load_dotenv

# Locates the .env file exactly where app.py lives, regardless of terminal path
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
from datetime import date, datetime
from zoneinfo import ZoneInfo

import importlib
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

import tracker.db as _db_mod
import tracker.networth as _nw_mod

_saved_defaults = _db_mod.Store.__init__.__defaults__
importlib.reload(_nw_mod)
importlib.reload(_db_mod)
_db_mod.Store.__init__.__defaults__ = _saved_defaults

from tracker import BUY, SELL, Transaction, compute_positions, companies
from tracker.db import Store
from tracker import performance as perf
from tracker.actions import apply_corporate_actions, resolve_actions
from tracker.assets import fund_list, stale_navs, stock_symbols
from tracker.calculator import (
    DEFAULT_FEE_RATE,
    fee_for_trade,
    shares_to_trim,
    simulate_dca,
    simulate_sell,
)
from tracker.chart import HEIGHT as CARD_HEIGHT, card_html
from tracker.history import EGX30_SYMBOL, fetch_history
from tracker.networth import (
    CAT_CASH,
    CAT_CD,
    CAT_FUND,
    CAT_FX,
    CAT_GOLD,
    CAT_US_STOCK,
    CD_CURRENCIES,
    COMMON_CURRENCIES,
    GOLD_PRESETS,
    NW_CHART_HEIGHT,
    NetWorthItem,
    build_snapshot_series,
    compute_net_worth,
    effective_fx_rate,
    effective_gold_prices,
    item_annual_income_egp,
    item_annual_income_native,
    item_currency,
    item_native_value,
    item_value_egp,
    nw_chart_html,
    snapshot_from_nw,
    snapshot_usd_exposure,
)
from tracker.portfolio import Dividend
from tracker.prices import fetch_latest_eodhd_close

CCY = "EGP"
OTHER = "__OTHER__"


def cairo_today() -> date:
    return datetime.now(ZoneInfo("Africa/Cairo")).date()


def cairo_now() -> datetime:
    return datetime.now(ZoneInfo("Africa/Cairo"))

st.set_page_config(page_title="Portfolio & Stock Tracker", page_icon=":chart_with_upwards_trend:", layout="wide")


@st.cache_resource
def get_store(_schema_version: int = 5) -> Store:
    s = Store()
    s.data_version = 0

    # Every write bumps the version, which is the cache key of load_data() below,
    # so the next read is guaranteed to hit the DB and the UI never shows stale data.
    def _invalidating(fn):
        def wrapper(*args, **kwargs):
            result = fn(*args, **kwargs)
            s.data_version += 1
            return result
        return wrapper

    for name in (
        "add_transaction", "update_transaction", "delete_transaction",
        "add_dividend", "update_dividend", "delete_dividend", "set_price",
        "add_corporate_action", "delete_corporate_action", "upsert_price_history", "set_fund",
        "set_setting", "add_net_worth_item", "update_net_worth_item", "delete_net_worth_item",
        "upsert_net_worth_snapshot", "delete_net_worth_snapshot",
    ):
        setattr(s, name, _invalidating(getattr(s, name)))
    return s


store = get_store()


@st.cache_data(show_spinner=False, max_entries=2)
def load_data(version: int, db_mtime_ns: int):
    """All DB reads in one cached call, keyed by the store's write version and the DB file mtime.

    The mtime makes changes made outside this process (scripts, restores) visible too.
    Returns only plain dicts/lists so it is always picklable by st.cache_data.
    """
    return (
        store.list_transactions(),
        store.list_dividends(),
        store.prices(),
        store.price_entries(),
        store.list_corporate_actions(),
        store.price_history(),
        store.fund_symbols(),
        store.settings(),
        store.list_net_worth_items(),
        store.list_net_worth_snapshots(),
    )

if "price_sync_message" not in st.session_state:
    st.session_state["price_sync_message"] = None


@st.cache_data(show_spinner=False)
def cached_logo_url(symbol: str) -> str | None:
    return companies.resolve_logo(symbol)


def initials_badge_uri(symbol: str) -> str:
    """An SVG data URI of a colored initials badge (for table image columns)."""
    initials = (symbol or "?")[:4]
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='40' height='40'>"
        "<rect width='40' height='40' rx='8' fill='#2b6cb0'/>"
        f"<text x='20' y='25' font-size='12' font-weight='700' fill='white' "
        f"text-anchor='middle' font-family='sans-serif'>{initials}</text></svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def logo_cell(symbol: str) -> str:
    """Verified logo URL if available, otherwise an initials-badge data URI."""
    return cached_logo_url(symbol) or initials_badge_uri(symbol)


def render_logo(symbol: str, size: int = 44) -> None:
    """Show the company logo (on a white tile), falling back to an initials badge."""
    url = cached_logo_url(symbol) if symbol else None
    if url:
        st.markdown(
            f"<img src='{url}' width='{size}' height='{size}' "
            f"style='border-radius:8px;background:#fff;padding:3px;object-fit:contain;'>",
            unsafe_allow_html=True,
        )
        return
    initials = (symbol or "?")[:4]
    st.markdown(
        f"<div style='width:{size}px;height:{size}px;border-radius:8px;"
        f"background:#2b6cb0;color:#fff;display:flex;align-items:center;"
        f"justify-content:center;font-weight:700;font-size:13px;'>{initials}</div>",
        unsafe_allow_html=True,
    )


def money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f} {CCY}"


def accounting_money(value: float | None) -> str:
    if value is None:
        return "—"
    amount = float(value)
    formatted = f"{abs(amount):,.0f}"
    return f"({formatted})" if amount < 0 else formatted


def egx_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{accounting_money(value)} {CCY}"


def price(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.4f}"


def pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:+.2f}%"


def freshness_label(updated_at: str | None) -> str:
    if not updated_at:
        return "Last refreshed: —"
    try:
        refreshed_on = datetime.fromisoformat(updated_at)
    except ValueError:
        return f"Last refreshed: {updated_at}"
    if refreshed_on.tzinfo is None:
        refreshed_on = refreshed_on.replace(tzinfo=ZoneInfo("Africa/Cairo"))
    else:
        refreshed_on = refreshed_on.astimezone(ZoneInfo("Africa/Cairo"))
    if refreshed_on.date() == cairo_today():
        return f"Last refreshed today ({refreshed_on.strftime('%Y-%m-%d %H:%M:%S %Z')})"
    return f"Last refreshed: {refreshed_on.strftime('%Y-%m-%d %H:%M:%S %Z')}"


def signed_css(val: str) -> str:
    """Green for positive, red for negative, grey for zero/blank."""
    if not isinstance(val, str) or val.strip() in ("", "—"):
        return ""
    s = val.strip()
    number = s.lstrip("+-−").replace(",", "").split(" ")[0].rstrip("%")
    try:
        amount = float(number)
    except ValueError:
        return ""
    if amount == 0:
        return "color: #9aa0a6"
    is_negative = s.startswith("-") or s.startswith("−")
    return "color: #e74c3c" if is_negative else "color: #2ecc71"


def badge(text: str, color: str) -> str:
    return (
        f"<span style='background:{color};color:#fff;padding:2px 10px;"
        f"border-radius:10px;font-size:12px;font-weight:600;'>{text}</span>"
    )


def _new_dialog_nonce() -> int:
    """Unique id per dialog open, used to namespace widget keys.

    Dismissing a dialog with the X leaves its widget state behind in session_state;
    a fresh nonce guarantees the next open starts from clean defaults.
    """
    st.session_state["_dialog_nonce"] = st.session_state.get("_dialog_nonce", 0) + 1
    return st.session_state["_dialog_nonce"]


def _company_picker(
    prefix: str, current: str | None, funds, allow_fund_flag: bool = True
) -> tuple[str, bool]:
    """Company selectbox (+ custom symbol box) with a live logo. Returns (symbol, is_fund)."""
    known_funds = sorted(s for s in funds if s not in companies.COMPANIES)
    options = sorted(companies.COMPANIES.keys()) + known_funds + [OTHER]
    default = current if current in options else OTHER

    def label(s: str) -> str:
        if s == OTHER:
            return "Other (type a symbol)…"
        return f"{s} · fund" if s in known_funds else companies.display_label(s)

    logo_col, pick_col = st.columns([1, 6], vertical_alignment="center")
    with pick_col:
        selected = st.selectbox(
            "Company", options, index=options.index(default), format_func=label,
            key=f"{prefix}sel_symbol",
        )
        flag_as_fund = False
        if selected == OTHER:
            custom_default = current if current and current not in options else ""
            symbol = st.text_input(
                "Symbol", value=custom_default, placeholder="e.g. RACC", key=f"{prefix}custom_symbol"
            ).strip().upper()
            if allow_fund_flag:
                flag_as_fund = st.checkbox(
                    "This is a fund (price entered manually, never synced from EODHD)",
                    value=symbol in funds, key=f"{prefix}is_fund",
                )
        else:
            symbol = selected
    with logo_col:
        render_logo(symbol)
    return symbol, (symbol in funds) or flag_as_fund


@st.dialog("🧾 Transaction", width="large")
def transaction_dialog(txn: dict | None, nonce: int, funds) -> None:
    """Add (txn=None) or edit a transaction. State lives only in this call's widgets."""
    editing = txn is not None
    p = f"txn{nonce}_"
    symbol, is_fund = _company_picker(p, txn["symbol"] if editing else None, funds)

    # Plain widgets (not a form) so the fee estimate below updates as quantity/price change.
    c1, c2 = st.columns(2)
    sides = [BUY, SELL]
    side = c1.selectbox(
        "Side", sides, index=sides.index(txn["side"]) if editing else 0,
        format_func=str.capitalize, key=f"{p}side",
    )
    txn_date = c2.date_input(
        "Date", value=date.fromisoformat(txn["date"]) if editing else cairo_today(), key=f"{p}date"
    )
    quantity = st.number_input(
        "Quantity", min_value=1, step=1, placeholder="0",
        value=int(txn["quantity"]) if editing else None, key=f"{p}qty",
    )
    c4, c5 = st.columns(2)
    unit_price = c4.number_input(
        f"Price ({CCY})", min_value=0.0, step=0.01, format="%.4f", placeholder="0.00",
        value=float(txn["price"]) if editing else None, key=f"{p}price",
    )
    fee = c5.number_input(
        f"Fee ({CCY})", min_value=0.0, step=0.01, format="%.2f", placeholder="auto",
        value=float(txn["fee"]) if editing else None, key=f"{p}fee",
    )
    auto_fee = fee_for_trade(quantity or 0, unit_price or 0, is_fund)
    if is_fund:
        st.caption(f"Fund: no fee. Left empty → **0.00 {CCY}**.")
    elif quantity and unit_price:
        st.caption(
            f"Fee left empty → **{auto_fee:,.2f} {CCY}** ({DEFAULT_FEE_RATE * 100:.3f}% of "
            f"{quantity * unit_price:,.2f})."
        )
    else:
        st.caption(f"Fee left empty → {DEFAULT_FEE_RATE * 100:.3f}% of the trade value.")
    st.write("")
    submitted = st.button(
        "Save changes" if editing else "Add transaction", type="primary",
        use_container_width=True, key=f"{p}save",
    )

    if submitted:
        if not symbol:
            st.error("Pick a company or type a symbol.")
        elif not quantity or not unit_price:
            st.error("Quantity and price must be greater than zero.")
        else:
            new_txn = Transaction(
                symbol=symbol, side=side, quantity=int(quantity), price=unit_price,
                fee=fee if fee is not None else auto_fee, date=txn_date.isoformat(),
            )
            if editing:
                store.update_transaction(int(txn["id"]), new_txn)
                verb = "Updated"
            else:
                store.add_transaction(new_txn)
                verb = "Added"
            if is_fund and symbol not in funds:
                store.set_fund(symbol, True)  # remembered, so it is priced manually from now on
            st.session_state["toast"] = f"{verb} {side} {int(quantity)} {symbol} @ {unit_price:g} {CCY}."
            st.rerun()  # full rerun without re-opening the dialog closes it


@st.dialog("💰 Dividend")
def dividend_dialog(nonce: int, funds) -> None:
    p = f"div{nonce}_"
    symbol, _ = _company_picker(p, None, funds, allow_fund_flag=False)

    with st.form(f"{p}form", border=False):
        dividend_date = st.date_input("Date", value=cairo_today(), key=f"{p}date")
        amount = st.number_input(
            f"Amount ({CCY})", min_value=0.0, step=0.01, format="%.2f",
            value=None, placeholder="0.00", key=f"{p}amount",
        )
        submitted = st.form_submit_button("Add dividend", type="primary", use_container_width=True)

    if submitted:
        if not symbol:
            st.error("Pick a company or type a symbol.")
        elif not amount:
            st.error("Amount must be greater than zero.")
        else:
            store.add_dividend(symbol=symbol, amount=float(amount), date=dividend_date.isoformat())
            st.session_state["toast"] = f"Added dividend of {amount:g} {CCY} for {symbol}."
            st.rerun()


def open_transaction_dialog(txn: dict | None = None) -> None:
    transaction_dialog(txn, _new_dialog_nonce(), frozenset(fund_symbols))



if "toast" in st.session_state:
    st.toast(st.session_state.pop("toast"))

(
    all_txn_rows, all_dividend_rows, saved_prices, saved_price_entries,
    all_action_rows, price_history, fund_rows, settings_rows, all_nw_rows, all_nw_snapshots,
) = load_data(store.data_version, os.stat(store.db_path).st_mtime_ns)
fund_symbols = set(fund_rows)  # symbols flagged as funds: priced by hand, never synced from EODHD
app_settings = dict(settings_rows)  # persisted preferences (deposit rate, size limit, ...)


def setting_float(key: str, default: float) -> float:
    """A saved numeric preference, falling back to ``default`` if missing or unreadable."""
    try:
        return float(app_settings[key])
    except (KeyError, TypeError, ValueError):
        return default


def live_funds() -> set:
    """Fund flags read straight from the database, never from a cached/stale copy.

    Used right before any EODHD request so that a fund can never be sent to the API, even if the
    page (or a fragment's arguments) was rendered before the flag changed.
    """
    return set(store.fund_symbols())


def record_current_snapshot(today_iso: str | None = None) -> dict:
    """Compute live Net Worth directly from the DB and upsert today's daily snapshot."""
    raw_txns = store.transactions()
    adj = apply_corporate_actions(raw_txns, store.list_corporate_actions())
    pos = compute_positions(adj, dividends=store.dividends(), prices=store.prices())
    funds = set(store.fund_symbols())
    items = store.net_worth_items()
    cfg = store.settings()

    def _cfg_f(k: str) -> float:
        try:
            return float(cfg[k])
        except (KeyError, TypeError, ValueError):
            return 0.0

    p24, p21 = effective_gold_prices(_cfg_f("gold_24k_price"), _cfg_f("gold_21k_price"))
    usd_r = _cfg_f("usd_egp_rate")
    nw = compute_net_worth(pos, funds, items, p24, p21, usd_r)
    snap = snapshot_from_nw(today_iso or cairo_today().isoformat(), nw)
    if snap["total_egp"] > 0:
        store.upsert_net_worth_snapshot(snap)
    return snap


def _save_setting(widget_key: str, setting_key: str) -> None:
    """on_change callback: write the widget's new value to the database."""
    store.set_setting(setting_key, st.session_state[widget_key])


def _save_nw_setting(widget_key: str, setting_key: str) -> None:
    """on_change callback for Net Worth rates (gold / USD): save setting and update today's snapshot."""
    store.set_setting(setting_key, st.session_state[widget_key])
    record_current_snapshot()
txns = [
    Transaction(
        symbol=r["symbol"], side=r["side"], quantity=int(r["quantity"]),
        price=r["price"], fee=r["fee"], date=r["date"],
    )
    for r in all_txn_rows
]
all_dividends_obj = [Dividend(symbol=r["symbol"], amount=r["amount"], date=r["date"]) for r in all_dividend_rows]

# Header is rendered AFTER the data loads: the Add-transaction dialog it opens needs the fund list,
# settings and other loaded data. It still renders before st.stop() below.
# --- Header ------------------------------------------------------------------
title_col, action_col = st.columns([5, 1], vertical_alignment="center")
with title_col:
    st.title("📈 Portfolio & Stock Tracker")
with action_col:
    if st.button("\\+ Add transaction", type="primary", use_container_width=True):
        open_transaction_dialog()

st.caption(
    "Tracks your *true* average across buy/sell round-trips — not the "
    "broker's moving average that drifts when you sell high and re-buy low."
)

if not txns:
    st.info("No transactions yet. Click **+ Add transaction** to get started.")
    st.stop()

# Bonus shares / splits become synthetic zero-cost buys, so every view below stays consistent.
adj_txns = apply_corporate_actions(txns, all_action_rows)
positions = compute_positions(adj_txns, dividends=all_dividends_obj, prices=saved_prices)
symbols = sorted({t.symbol for t in txns})

(tab_portfolio, tab_allocation, tab_perf, tab_dca, tab_prices, tab_txns, tab_dividends, tab_networth) = st.tabs(
    [
        "📊 Portfolio", "🥧 Allocation", "📈 Performance", "🧮 Buy / Sell Planner",
        "💲 Prices", "🧾 Transactions", "💰 Dividends", "🏦 Net Worth",
    ]
)

# --- Portfolio tab -----------------------------------------------------------
from html import escape as _esc

GREEN, RED, GREY = "#2ecc71", "#e74c3c", "#9aa0a6"
_TONE_BG = {GREEN: "rgba(46,204,113,.15)", RED: "rgba(231,76,60,.15)", GREY: "rgba(154,160,166,.15)"}

PORTFOLIO_CSS = """
<style>
.pf-card{background:#1c1f26;border:1px solid #2a2e37;border-radius:14px;padding:16px 18px;margin-bottom:14px}
.pf-head{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.pf-logo{border-radius:10px;background:#fff;padding:3px;object-fit:contain;flex:none}
.pf-initials{background:#2b6cb0;color:#fff;display:flex;align-items:center;justify-content:center;
  font-weight:700;font-size:13px;padding:0}
.pf-id{flex:1;min-width:0}
.pf-sym{font-weight:700;font-size:1.05rem;line-height:1.2}
.pf-name{color:#9aa0a6;font-size:.78rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pf-pill{font-weight:700;font-size:.8rem;padding:3px 10px;border-radius:999px;white-space:nowrap}
.pf-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 14px}
.pf-l{display:block;color:#9aa0a6;font-size:.7rem;text-transform:uppercase;letter-spacing:.05em}
.pf-v{font-weight:600;font-size:1rem}
.pf-hero{font-size:1.25rem;font-weight:700}
.pf-card details{margin-top:14px;border-top:1px solid #2a2e37;padding-top:8px}
.pf-card summary{cursor:pointer;color:#9aa0a6;font-size:.8rem}
.pf-rows{display:grid;grid-template-columns:1fr auto;gap:4px 12px;margin-top:8px;font-size:.85rem}
.pf-rows span:nth-child(odd){color:#9aa0a6}
.pf-rows span:nth-child(even){text-align:right;font-weight:600}
.al-row{display:grid;grid-template-columns:130px 1fr 64px 190px;align-items:center;gap:12px;
  background:#1c1f26;border:1px solid #2a2e37;border-left:4px solid #2a2e37;border-radius:10px;
  padding:9px 14px;margin-bottom:8px}
.al-id{display:flex;align-items:center;gap:8px;font-weight:700}
.al-track{position:relative;height:12px;background:#262a33;border-radius:6px}
.al-fill{position:absolute;left:0;top:0;height:12px;border-radius:6px}
.al-mark{position:absolute;top:-4px;width:2px;height:20px;background:#e6e6e6;opacity:.85}
.al-w{font-weight:700;text-align:right}
.al-meta{font-size:.78rem;color:#9aa0a6;line-height:1.35}
.al-flag{font-weight:700;font-size:.72rem;padding:1px 8px;border-radius:999px;margin-left:4px;white-space:nowrap}
[data-testid="stMetric"]{background:#1c1f26;border:1px solid #2a2e37;border-radius:14px;padding:14px 16px;
  min-height:8.25rem;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center}
</style>
"""


def _tone(value: float | None) -> str:
    if value is None or abs(value) < 1e-9:
        return GREY
    return GREEN if value > 0 else RED


def _signed(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):,.0f}{suffix}"


def _colored(text: str, value: float | None) -> str:
    return f"<span style='color:{_tone(value)}'>{text}</span>"


def logo_html(symbol: str, size: int = 40) -> str:
    url = cached_logo_url(symbol) if symbol else None
    if url:
        return f"<img class='pf-logo' src='{_esc(url)}' width='{size}' height='{size}'>"
    return f"<div class='pf-logo pf-initials' style='width:{size}px;height:{size}px'>{_esc((symbol or '?')[:4])}</div>"


def position_card_html(p) -> str:
    """One holding as a single HTML block (one element instead of a dozen widgets)."""
    is_fund_pos = p.symbol in fund_symbols
    name = "Fund · manual NAV" if is_fund_pos else ((companies.COMPANIES.get(p.symbol) or {}).get("name") or "")
    pill_tone = _tone(p.breakeven_pct)
    pill = (
        f"<span class='pf-pill' style='color:{pill_tone};background:{_TONE_BG[pill_tone]}'>"
        f"{pct(p.breakeven_pct)}</span>"
        if p.breakeven_pct is not None
        else ""
    )

    def cell(label: str, value: str, cls: str = "pf-v") -> str:
        return f"<div><span class='pf-l'>{label}</span><span class='{cls}'>{value}</span></div>"

    be = price(p.breakeven) if p.breakeven is not None else "—"
    grid = "".join(
        [
            cell("Break-even", be, "pf-hero"),
            cell("Current price", price(p.current_price), "pf-hero"),
            cell("Qty held", f"{p.quantity:,}"),
            cell("Market value", egx_money(p.market_value) if p.market_value is not None else "—"),
            cell("Total P&amp;L", _colored(_signed(p.total_pnl, f" {CCY}"), p.total_pnl)),
            cell("Total return", _colored(pct(p.total_return_pct), p.total_return_pct)),
        ]
    )

    def row(label: str, value: str) -> str:
        return f"<span>{label}</span><span>{value}</span>"

    rows = "".join(
        [
            row("Unrealized P&amp;L", _colored(_signed(p.unrealized_pnl, f" {CCY}"), p.unrealized_pnl)),
            row("Unrealized %", _colored(pct(p.unrealized_pct), p.unrealized_pct)),
            row("Realized P&amp;L", _colored(_signed(p.realized_pnl, f" {CCY}"), p.realized_pnl)),
            row("Net invested", egx_money(p.net_invested)),
            row("Broker avg (THNDR)", price(p.broker_avg) if p.quantity > 0 else "—"),
            row("Pure avg buy", price(p.pure_avg_buy)),
        ]
    )
    return (
        "<div class='pf-card'>"
        f"<div class='pf-head'>{logo_html(p.symbol)}"
        f"<div class='pf-id'><div class='pf-sym'>{_esc(p.symbol)}</div><div class='pf-name'>{_esc(name)}</div></div>"
        f"{pill}</div>"
        f"<div class='pf-grid'>{grid}</div>"
        f"<details><summary>More details</summary><div class='pf-rows'>{rows}</div></details>"
        "</div>"
    )


def allocation_rows(held, limit_pct: float):
    """Weights per holding. Uses market value, falling back to cost basis when unpriced."""
    entries = []
    for p in held:
        estimated = p.market_value is None
        entries.append((p, p.cost_basis_remaining if estimated else p.market_value, estimated))
    total_now = sum(v for _, v, _ in entries) or 1.0
    total_cost = sum(p.cost_basis_remaining for p, _, _ in entries) or 1.0
    rows = []
    for p, value, estimated in entries:
        w = value / total_now * 100
        cw = p.cost_basis_remaining / total_cost * 100
        oversized = w > limit_pct and p.symbol not in fund_symbols  # the size limit is for single stocks
        red = p.breakeven_pct is not None and p.breakeven_pct < 0
        rows.append(
            {"p": p, "value": value, "estimated": estimated, "w": w, "cw": cw,
             "oversized": oversized, "red": red}
        )
    rows.sort(key=lambda r: -r["w"])
    return rows


def allocation_html(rows) -> str:
    max_w = max((max(r["w"], r["cw"]) for r in rows), default=1.0) or 1.0
    out = []
    for r in rows:
        p = r["p"]
        tone = _tone(p.breakeven_pct)
        fill = min(r["w"] / max_w * 100, 100)
        mark = min(r["cw"] / max_w * 100, 100)
        if r["oversized"] and r["red"]:
            border, flag = RED, f"<span class='al-flag' style='color:{RED};background:{_TONE_BG[RED]}'>Oversized &amp; in the red</span>"
        elif r["oversized"]:
            border, flag = "#f1c40f", "<span class='al-flag' style='color:#f1c40f;background:rgba(241,196,15,.15)'>Oversized</span>"
        else:
            border, flag = "#2a2e37", ""
        delta = r["w"] - r["cw"]
        approx = "≈" if r["estimated"] else ""
        pill = (
            f"<span class='al-flag' style='color:{tone};background:{_TONE_BG[tone]}'>{pct(p.breakeven_pct)}</span>"
            if p.breakeven_pct is not None
            else ""
        )
        out.append(
            f"<div class='al-row' style='border-left-color:{border}'>"
            f"<div class='al-id'>{logo_html(p.symbol, 28)}{_esc(p.symbol)}"
            + ("<span class='al-flag' style='color:#9aa0a6;background:rgba(154,160,166,.15)'>FUND</span>" if p.symbol in fund_symbols else "")
            + "</div>"
            
            f"<div class='al-track'><div class='al-fill' style='width:{fill:.1f}%;background:{tone}'></div>"
            f"<div class='al-mark' style='left:{mark:.1f}%' title='Weight at cost'></div></div>"
            f"<div class='al-w'>{approx}{r['w']:.1f}%</div>"
            f"<div class='al-meta'>{approx}{egx_money(r['value'])}{pill}{flag}<br>"
            f"at cost {r['cw']:.1f}% · <span style='color:{_tone(delta)}'>{delta:+.1f} pp</span></div>"
            "</div>"
        )
    return "".join(out)


PORTFOLIO_SORTS = {
    "Most underwater": lambda p: p.breakeven_pct if p.breakeven_pct is not None else float("inf"),
    "Market value": lambda p: -(p.market_value or 0),
    "Total P&L": lambda p: -(p.total_pnl or 0),
    "Symbol": lambda p: p.symbol,
}


@st.fragment
def portfolio_tab(positions) -> None:
    st.markdown(PORTFOLIO_CSS, unsafe_allow_html=True)

    total_invested = sum(p.net_invested for p in positions)
    total_market = sum(p.market_value for p in positions if p.market_value is not None)
    total_realized = sum(p.realized_pnl for p in positions)
    total_unrealized = sum(p.unrealized_pnl for p in positions if p.unrealized_pnl is not None)
    total_buy_cost = sum(p.total_buy_cost for p in positions)
    total_pnl = total_realized + total_unrealized
    total_return_pct = (total_pnl / total_buy_cost) if total_buy_cost > 0 else None
    unrealized_pct = (total_unrealized / total_market) if total_market else None

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net invested", money(total_invested))
    m2.metric("Market value", money(total_market), pct(unrealized_pct) if unrealized_pct is not None else None)
    m3.metric("Realized P&L", money(total_realized))
    m4.metric("Total P&L", money(total_pnl), pct(total_return_pct) if total_return_pct is not None else None)

    held = [p for p in positions if p.quantity > 0]
    closed = [p for p in positions if p.quantity <= 0]

    head_col, sort_col = st.columns([1, 2], vertical_alignment="center")
    head_col.subheader(f"Holdings ({len(held)})")
    sort_key = sort_col.radio(
        "Sort by", list(PORTFOLIO_SORTS), horizontal=True, key="pf_sort", label_visibility="collapsed"
    )

    held.sort(key=PORTFOLIO_SORTS[sort_key])
    per_row = 3
    for start in range(0, len(held), per_row):
        cols = st.columns(per_row)
        for col, p in zip(cols, held[start : start + per_row]):
            col.markdown(position_card_html(p), unsafe_allow_html=True)

    if closed:
        with st.expander(f"Closed positions ({len(closed)})"):
            pnl_col, ret_col = f"Realized P&L ({CCY})", "Total return %"
            closed_df = pd.DataFrame(
                [
                    {
                        "Logo": logo_cell(p.symbol),
                        "Symbol": p.symbol,
                        pnl_col: p.realized_pnl,
                        ret_col: (p.total_return_pct or 0) * 100,
                    }
                    for p in closed
                ]
            )

            def _sign_color(v: float) -> str:
                return f"color: {_tone(v)}; font-weight: 600"

            st.dataframe(
                closed_df.style.map(_sign_color, subset=[pnl_col, ret_col]).format(
                    {pnl_col: lambda v: _signed(v), ret_col: "{:+.2f}%"}
                ),
                hide_index=True,
                use_container_width=True,
                column_config={"Logo": st.column_config.ImageColumn(" ", width="small")},
            )

    with st.expander("How to read this"):
        st.markdown(
            "- **Break-even** = (money in − money out) ÷ shares held — your *true* average, "
            "including gains you already locked in.\n"
            "- **Pill** = how far the current price is above/below your break-even. Negative means "
            "you're genuinely down, which can be a real DCA opportunity.\n"
            "- **Broker avg (THNDR)** is the moving average that drifts on re-buys; "
            "**Pure avg buy** ignores sells entirely. Both are under *More details*."
        )


@st.fragment
def allocation_tab(positions) -> None:
    st.markdown(PORTFOLIO_CSS, unsafe_allow_html=True)
    held = [p for p in positions if p.quantity > 0]
    if not held:
        st.info("No open positions yet.")
        return

    head, limit_col = st.columns([2, 1], vertical_alignment="bottom")
    head.caption(
        "How your wallet is split across open positions. Bar = weight now (green/red by return vs "
        "break-even); white tick = weight at cost; pp = how much it has grown or shrunk relative "
        "to the rest."
    )
    limit_pct = limit_col.number_input(
        "Flag positions above (%)", min_value=1.0, max_value=100.0,
        value=setting_float("size_limit", 20.0), step=1.0, key="pf_limit",
        on_change=_save_setting, args=("pf_limit", "size_limit"),
        help="Positions heavier than this are flagged as oversized. Saved automatically.",
    )

    rows = allocation_rows(held, limit_pct)
    top3 = sum(r["w"] for r in rows[:3])
    oversized = [r for r in rows if r["oversized"]]
    risky = [r for r in oversized if r["red"]]

    m1, m2, m3 = st.columns(3)
    m1.metric("Largest position", f"{rows[0]['p'].symbol} · {rows[0]['w']:.1f}%", delta_color="off")
    m2.metric("Top 3 share", f"{top3:.1f}%", delta_color="off")
    m3.metric(
        f"Above {limit_pct:g}%", f"{len(oversized)} position(s)",
        f"{len(risky)} in the red" if risky else None, delta_color="inverse",
    )

    st.markdown(allocation_html(rows), unsafe_allow_html=True)


with tab_portfolio:
    portfolio_tab(positions)

with tab_allocation:
    allocation_tab(positions)

# --- Prices tab --------------------------------------------------------------
def _fetch_close(sym: str, api_key: str):
    """The ONLY path to EODHD's price endpoint. Funds have no feed, so they are refused here."""
    if sym in live_funds():
        return sym, None
    try:
        return sym, fetch_latest_eodhd_close(sym, api_key)
    except Exception:
        return sym, None


def _fetch_hist(sym: str, start: str, api_key: str):
    """The ONLY path to EODHD's history endpoint. Funds are refused here too."""
    if sym in live_funds():
        return None, "fund: priced manually"
    return fetch_history(sym, start, api_key)


@st.fragment
def prices_tab(
    open_symbols: list[str],
    all_symbols: list[str],
    funds: set,
    saved_prices: dict,
    saved_price_entries: dict,
) -> None:
    """Stocks sync from EODHD; funds have no feed, so their NAV is entered by hand.

    Only open positions are listed/synced: a closed position needs no price, and every symbol
    synced costs one EODHD call (free plan: 20/day).
    """
    stocks = stock_symbols(open_symbols, funds)
    held_funds = fund_list(open_symbols, funds)
    closed = [s for s in all_symbols if s not in open_symbols]

    st.caption(
        "Stocks sync from EODHD. Funds (e.g. Beltone) have no price feed — enter their NAV by hand."
    )

    # --- which symbols are funds ---------------------------------------------------------
    with st.expander(f"Asset types · {len(funds & set(all_symbols))} fund(s)"):
        # The key changes with the stored selection, so a flag changed elsewhere (e.g. in the
        # Add transaction dialog) is never overwritten by stale widget state.
        picker_key = "fund_picker_" + "_".join(sorted(funds))
        chosen = st.multiselect(
            "Funds (priced manually, never synced)",
            sorted(all_symbols),
            default=[s for s in sorted(all_symbols) if s in funds],
            key=picker_key,
            help="Everything not listed here is treated as an EGX stock and synced from EODHD.",
        )
        changes = [(s, s in chosen) for s in all_symbols if (s in chosen) != (s in funds)]
        if changes:
            for s, flag in changes:
                store.set_fund(s, flag)
            st.session_state["toast"] = "Updated: " + ", ".join(
                f"{s} → {'fund' if f else 'stock'}" for s, f in changes
            )
            st.rerun()

    # --- EODHD sync (stocks only) --------------------------------------------------------
    sync_col, api_col = st.columns([1, 1], vertical_alignment="bottom")
    with api_col:
        eodhd_api_key = st.text_input(
            "🔑 EODHD API Key",
            value=os.getenv("EODHD_API_KEY", ""),
            type="password",
            placeholder="Paste token or set env variable...",
            help="Free plan: 20 calls/day. Only open stocks are synced (one call each).",
            key="tab_eodhd_api_key",
        )

    if st.session_state.get("price_sync_message"):
        st.info(st.session_state["price_sync_message"])

    sync_disabled = not eodhd_api_key.strip() or not stocks
    with sync_col:
        if not eodhd_api_key.strip():
            st.caption("⚠️ *Provide an API key to enable sync*")
        sync_clicked = st.button(
            f"Sync {len(stocks)} stock(s) from EODHD",
            type="primary",
            use_container_width=True,
            disabled=sync_disabled,
        )

    if sync_clicked:
        stocks = stock_symbols(stocks, live_funds())  # re-check against the DB, not the page's copy
        timestamp = cairo_now().isoformat(timespec="seconds")
        updated, unchanged, skipped = [], [], []
        # Fetch all stocks concurrently instead of one blocking call at a time.
        with st.status(f"Syncing {len(stocks)} stock(s)…", expanded=False) as status:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda s: _fetch_close(s, eodhd_api_key), stocks))
            for sym, snapshot in results:
                if snapshot is None:
                    skipped.append(sym)
                    continue
                existing = saved_prices.get(sym)
                if existing is None or abs(existing - snapshot.price) > 1e-9:
                    store.set_price(sym, snapshot.price, timestamp)
                    updated.append(sym)
                else:
                    unchanged.append(sym)
            status.update(label="Sync complete", state="complete")

        if updated or unchanged:
            record_current_snapshot(timestamp[:10])
        parts: list[str] = []
        if updated:
            parts.append(f"Updated {len(updated)} stock(s): {', '.join(updated)}.")
        if unchanged:
            parts.append(f"{len(unchanged)} stock(s) already matched the latest close.")
        if skipped:
            parts.append(f"Skipped {len(skipped)} with no EODHD match: {', '.join(skipped)}.")
        if held_funds:
            parts.append(f"Funds are priced manually, so {', '.join(held_funds)} {'was' if len(held_funds) == 1 else 'were'} not synced.")
        if not (updated or unchanged or skipped):
            parts.insert(0, "No prices were returned by EODHD.")
        st.session_state["price_sync_message"] = " ".join(parts)
        st.rerun()  # full rerun so the Portfolio tab picks up new prices

    # --- stale NAV reminder ---------------------------------------------------------------
    stale = stale_navs(held_funds, saved_price_entries, cairo_today())
    if stale:
        st.warning(
            "Update fund NAV: "
            + ", ".join(f"**{s}** ({'never priced' if age is None else f'{age} days old'})" for s, age in stale)
        )

    # --- manual price inputs (one form, one Save button) -------------------------------------
    def price_box(sym: str, is_fund: bool) -> None:
        head_logo, head_name = st.columns([1, 4], vertical_alignment="center")
        with head_logo:
            render_logo(sym, size=28)
        head_name.markdown(f"**{sym}**" + ("  ·  fund" if is_fund else ""))
        saved = saved_prices.get(sym)
        st.number_input(
            f"{'NAV' if is_fund else 'Price'} ({CCY})",
            min_value=0.0,
            step=0.01,
            value=float(saved) if saved else None,
            format="%.4f",
            placeholder="0.00",
            key=f"price_{sym}",
            label_visibility="collapsed",
        )
        st.caption(freshness_label(saved_price_entries.get(sym, {}).get("updated_at")))

    with st.form("prices_form", border=False):
        for title, group, is_fund in (
            ("Stocks", stocks, False),
            ("Funds — enter NAV manually", held_funds, True),
        ):
            if not group:
                continue
            st.markdown(f"##### {title}")
            pcols = st.columns(min(4, len(group)))
            for i, sym in enumerate(group):
                with pcols[i % len(pcols)]:
                    price_box(sym, is_fund)
        save_prices = st.form_submit_button("Save prices", type="primary")

    if closed:
        st.caption("No price needed (closed positions): " + ", ".join(closed))

    if save_prices:
        timestamp = cairo_now().isoformat(timespec="seconds")
        changed = 0
        for sym in stocks + held_funds:
            new_price = st.session_state.get(f"price_{sym}")
            if new_price and new_price != saved_prices.get(sym):
                store.set_price(sym, new_price, timestamp)
                changed += 1
        if changed:
            record_current_snapshot(timestamp[:10])
            st.session_state["toast"] = f"Saved {changed} price(s)."
            st.rerun()
        else:
            st.toast("No price changes to save.")

    # --- indexes (manual benchmark level, used by the Performance tab comparison) -------------
    st.markdown("##### Indexes")
    with st.form("indexes_form", border=False):
        idx_col, _ = st.columns([1, 3])
        with idx_col:
            st.markdown("**EGX30**  ·  index level")
            saved_idx = saved_prices.get(EGX30_SYMBOL)
            st.number_input(
                "EGX30 level", min_value=0.0, step=0.01, format="%.2f",
                value=float(saved_idx) if saved_idx else None, placeholder="0.00",
                key="index_egx30", label_visibility="collapsed",
            )
            st.caption(freshness_label(saved_price_entries.get(EGX30_SYMBOL, {}).get("updated_at")))
        save_index = st.form_submit_button("Save index", type="primary")
    st.caption("Enter the level once a day (e.g. at the close); the Performance tab compares you against it.")
    if save_index:
        level = st.session_state.get("index_egx30")
        if level and level != saved_prices.get(EGX30_SYMBOL):
            store.set_price(EGX30_SYMBOL, level, cairo_now().isoformat(timespec="seconds"))
            st.session_state["toast"] = "Saved EGX30 level."
            st.rerun()
        else:
            st.toast("No index change to save.")


# --- Transactions tab --------------------------------------------------------
def _side_css(val: str) -> str:
    return "color: #2ecc71; font-weight: 600" if val == "BUY" else "color: #e74c3c; font-weight: 600"


@st.dialog("🎁 Corporate action")
def corporate_action_dialog(nonce: int) -> None:
    """Bonus shares or a forward split: free shares that lower your average cost."""
    p = f"ca{nonce}_"
    sym = st.selectbox(
        "Stock", stock_symbols(symbols, fund_symbols), format_func=companies.display_label, key=f"{p}sym"
    )
    kind = st.radio("Type", ["Bonus shares", "Split"], horizontal=True, key=f"{p}kind")
    when = st.date_input("Date shares are credited", value=cairo_today(), key=f"{p}date")

    if kind == "Bonus shares":
        c1, c2 = st.columns(2)
        new_shares = int(c1.number_input("Bonus shares", min_value=1, value=1, step=1, key=f"{p}new"))
        per_held = int(c2.number_input("for every … held", min_value=1, value=10, step=1, key=f"{p}per"))
        db_kind = "bonus"
        st.caption(f"{new_shares} free share(s) for every {per_held} you hold.")
    else:
        ratio = int(st.number_input("Split into … shares per 1", min_value=2, value=2, step=1, key=f"{p}ratio"))
        new_shares, per_held, db_kind = ratio - 1, 1, "split"
        st.caption(f"1 share becomes {ratio}. Reverse splits are not supported.")

    preview = resolve_actions(
        txns,
        all_action_rows + [{
            "id": 10**9, "symbol": sym, "date": when.isoformat(), "kind": db_kind,
            "new_shares": new_shares, "per_held": per_held,
        }],
    )
    mine = next(r for r in preview if r["id"] == 10**9)
    st.info(f"You hold **{mine['held']:,}** {sym} on that date → **+{mine['extra']:,}** shares credited.")
    st.caption("Fractional entitlements are rounded down. Cost stays the same, so your average drops.")

    if st.button("Save corporate action", type="primary", use_container_width=True, key=f"{p}save"):
        if mine["extra"] <= 0:
            st.error("This would credit 0 shares — check the date and the ratio.")
        else:
            store.add_corporate_action(sym, when.isoformat(), db_kind, new_shares, per_held)
            st.session_state["toast"] = f"Added {kind.lower()} for {sym}: +{mine['extra']:,} shares."
            st.rerun()


def _action_label(a: dict) -> str:
    if a["kind"] == "split":
        return f"{a['new_shares'] + 1}-for-1 split"
    return f"Bonus {a['new_shares']} per {a['per_held']}"


@st.fragment
def txns_tab(all_ledger: list[dict]) -> None:
    act_col, _ = st.columns([1, 3])
    if act_col.button("🎁 Corporate action", use_container_width=True, help="Bonus shares or a split"):
        corporate_action_dialog(_new_dialog_nonce())

    if all_action_rows:
        resolved = resolve_actions(txns, all_action_rows)
        with st.expander(f"Corporate actions ({len(resolved)})"):
            for a in sorted(resolved, key=lambda x: x["date"], reverse=True):
                c1, c2 = st.columns([6, 1], vertical_alignment="center")
                c1.markdown(
                    f"**{a['symbol']}** · {a['date']} · {_action_label(a)} · held {a['held']:,} → "
                    f"**+{a['extra']:,}** shares"
                )
                with c2.popover("🗑"):
                    st.write("Delete this action?")
                    if st.button("Confirm", type="primary", key=f"ca_del_{a['id']}"):
                        store.delete_corporate_action(a["id"])
                        st.session_state["toast"] = "Corporate action deleted."
                        st.rerun()

    symbol_options = ["All stocks"] + sorted({r["symbol"] for r in all_ledger})
    selected_symbol = st.selectbox("Filter by stock symbol", symbol_options, key="txn_symbol_filter")
    ledger = [r for r in all_ledger if selected_symbol == "All stocks" or r["symbol"] == selected_symbol]

    df = pd.DataFrame(
        [
            {
                "Date": r["date"],
                "Symbol": r["symbol"],
                "Side": "BUY" if r["side"] == BUY else "SELL",
                "Qty": r["quantity"],
                "Price": r["price"],
                "Fee": r["fee"],
                "Total": r["quantity"] * r["price"] + (r["fee"] if r["side"] == BUY else -r["fee"]),
            }
            for r in ledger
        ],
        columns=["Date", "Symbol", "Side", "Qty", "Price", "Fee", "Total"],
    )
    event = st.dataframe(
        df.style.map(_side_css, subset=["Side"]),
        use_container_width=True,
        hide_index=True,
        on_select="rerun",  # reruns only this fragment
        selection_mode="single-row",
        height=min(40 + 35 * max(len(df), 1), 600),
        key="txn_table",
        column_config={
            "Qty": st.column_config.NumberColumn(format="%d"),
            "Price": st.column_config.NumberColumn(format="%.4f"),
            "Fee": st.column_config.NumberColumn(format="%.2f"),
            "Total": st.column_config.NumberColumn(f"Total ({CCY})", format="%.2f"),
        },
    )

    rows = event.selection.rows
    if not rows:
        st.caption("Select a row to edit or delete it.")
        return
    r = ledger[rows[0]]
    st.caption(f"Selected: **{r['date']} · {r['side'].upper()} {r['quantity']:g} {r['symbol']}**")
    edit_col, del_col, _ = st.columns([1, 1, 4])
    if edit_col.button("✏️ Edit", key="txn_edit", use_container_width=True):
        open_transaction_dialog(r)
    with del_col.popover("🗑 Delete", use_container_width=True):
        st.write("Delete this transaction?")
        if st.button("Confirm delete", type="primary", key="txn_confirm_delete"):
            store.delete_transaction(r["id"])
            st.session_state["toast"] = "Transaction deleted."
            st.rerun()


# --- Dividends tab ------------------------------------------------------------
@st.fragment
def dividends_tab(all_dividends: list[dict]) -> None:
    if st.button("➕ Add Dividend"):
        dividend_dialog(_new_dialog_nonce(), frozenset(fund_symbols))

    symbol_options = ["All stocks"] + sorted({r["symbol"] for r in all_dividends})
    selected_symbol = st.selectbox("Filter by stock symbol", symbol_options, key="dividend_symbol_filter")
    dividends = [r for r in all_dividends if selected_symbol == "All stocks" or r["symbol"] == selected_symbol]

    df = pd.DataFrame(
        [{"Date": r["date"], "Symbol": r["symbol"], f"Amount ({CCY})": r["amount"]} for r in dividends],
        columns=["Date", "Symbol", f"Amount ({CCY})"],
    )
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        height=min(40 + 35 * max(len(df), 1), 600),
        key="div_table",
        column_config={f"Amount ({CCY})": st.column_config.NumberColumn(format="%.2f")},
    )

    rows = event.selection.rows
    if not rows:
        st.caption("Select a row to delete it.")
        return
    r = dividends[rows[0]]
    st.caption(f"Selected: **{r['date']} · {r['symbol']} · {r['amount']:,.2f} {CCY}**")
    with st.popover("🗑 Delete"):
        st.write("Delete this dividend?")
        if st.button("Confirm delete", type="primary", key="div_confirm_delete"):
            store.delete_dividend(r["id"])
            st.session_state["toast"] = "Dividend deleted."
            st.rerun()


with tab_prices:
    prices_tab(
        [p.symbol for p in positions if p.quantity > 0], symbols, fund_symbols,
        saved_prices, saved_price_entries,
    )

with tab_txns:
    txns_tab(list(reversed(all_txn_rows)))

with tab_dividends:
    dividends_tab(list(reversed(all_dividend_rows)))


# --- Buy / Sell planner tab ----------------------------------------------------
def sell_planner(held: dict, saved_prices: dict, funds) -> None:
    st.caption(
        "Simulate selling or trimming: cash you'd receive, profit locked in, what's left, "
        "and how your break-even and wallet share change."
    )
    inp, out = st.columns([1, 2], gap="large")

    with inp:
        sym = st.selectbox("Stock", sorted(held), format_func=companies.display_label, key="sell_symbol")
        p = held[sym]
        saved = saved_prices.get(sym)
        sell_price = st.number_input(
            f"Sell price ({CCY})", min_value=0.0, step=0.01, format="%.4f", placeholder="0.00",
            value=float(saved) if saved else None, key=f"sell_price_{sym}",
        )
        others = sum(
            (q.market_value if q.market_value is not None else q.cost_basis_remaining)
            for s_, q in held.items() if s_ != sym
        )
        mode = st.radio("Size the sale by", ["Shares", "Trim to % of wallet"], horizontal=True, key="sell_mode")
        if mode == "Shares":
            sell_qty = int(
                st.number_input(
                    "Shares to sell", min_value=1, max_value=p.quantity, step=1,
                    value=max(1, p.quantity // 4), key=f"sell_qty_{sym}",
                )
            )
        else:
            target = st.number_input(
                "Target share of wallet (%)", min_value=1.0, max_value=99.0, step=1.0,
                value=float(min(st.session_state.get("pf_limit", 20.0), 99.0)), key=f"sell_target_{sym}",
            )
            sell_qty = shares_to_trim(p.quantity, sell_price, others, target) if sell_price else 0
            st.caption(f"→ sell **{sell_qty:,}** shares")
        fee = st.number_input(
            f"Fee ({CCY})", min_value=0.0, step=1.0, value=None, placeholder="auto", key="sell_fee",
            help=f"Total brokerage fee for this sale. Empty = {DEFAULT_FEE_RATE * 100:.3f}% of the trade value (0 for funds).",
        )
        st.caption(
            f"Current position: **{p.quantity:,}** shares · broker avg **{price(p.broker_avg)}** · "
            f"break-even **{price(p.breakeven)}**"
        )

    with out:
        if not sell_price:
            st.info("Enter a sell price to run the simulation.")
            return
        if sell_qty < 1:
            st.info("Nothing to sell: the position is already at or below that share of your wallet.")
            return

        r = simulate_sell(
            p.quantity, p.broker_avg, p.net_invested, sell_price, sell_qty,
            fee if fee else fee_for_trade(sell_qty, sell_price, sym in funds),
        )
        value_before = p.quantity * sell_price
        value_after = r.new_qty * sell_price
        share_before = value_before / (others + value_before) * 100 if others + value_before else 0.0
        share_after = value_after / (others + value_after) * 100 if others + value_after else 0.0

        c1, c2, c3 = st.columns(3)
        c1.metric("Cash received", money(r.proceeds), f"{sell_qty:,} shares sold", delta_color="off")
        c2.metric(
            "Profit locked in", money(r.realized_pnl),
            pct(r.pct_at_price) + " vs broker avg" if r.pct_at_price is not None else None,
        )
        c3.metric(
            "Shares left", f"{r.new_qty:,}", f"{-sell_qty:+,}", delta_color="off",
        )

        c4, c5, c6 = st.columns(3)
        if r.new_breakeven is None:
            c4.metric("New break-even", "—", "position closed", delta_color="off")
        else:
            c4.metric(
                "New break-even", price(r.new_breakeven),
                f"{r.new_breakeven - p.breakeven:+,.4f}", delta_color="inverse",
            )
        c5.metric("Broker avg", price(r.broker_avg), "unchanged by a sell", delta_color="off")
        c6.metric(
            "Share of wallet", f"{share_after:.1f}%", f"{share_after - share_before:+.1f} pp",
            delta_color="off", help="Position value ÷ open positions at the sell price; proceeds leave the wallet.",
        )

    st.subheader("Compare sale sizes")
    rows = []
    for frac in (0.1, 0.25, 0.5, 0.75, 1.0):
        q = max(1, min(p.quantity, round(p.quantity * frac)))
        s = simulate_sell(
            p.quantity, p.broker_avg, p.net_invested, sell_price, q,
            fee if fee else fee_for_trade(q, sell_price, sym in funds),
        )
        left_value = s.new_qty * sell_price
        rows.append(
            {
                "Sell (% of holding)": f"{frac:.0%}",
                "Shares": q,
                f"Cash ({CCY})": s.proceeds,
                f"Profit ({CCY})": s.realized_pnl,
                "Shares left": s.new_qty,
                "New break-even": s.new_breakeven,
                "Wallet share": left_value / (others + left_value) * 100 if others + left_value else 0.0,
            }
        )
    cmp_df = pd.DataFrame(rows)
    pnl_col = f"Profit ({CCY})"
    st.dataframe(
        cmp_df.style.map(lambda v: f"color: {_tone(v)}; font-weight: 600", subset=[pnl_col]).format(
            {
                "Shares": "{:,}",
                f"Cash ({CCY})": "{:,.0f}",
                pnl_col: lambda v: _signed(v),
                "Shares left": "{:,}",
                "New break-even": lambda v: "—" if v is None or v != v else f"{v:,.4f}",
                "Wallet share": "{:.1f}%",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )


@st.fragment
def dca_tab(positions, saved_prices: dict, funds) -> None:
    held = {p.symbol: p for p in positions if p.quantity > 0}
    if not held:
        st.info("No open positions to plan with.")
        return

    action = st.radio(
        "Plan", ["Buy more (DCA)", "Sell / trim"], horizontal=True, key="plan_action",
        label_visibility="collapsed",
    )
    if action == "Sell / trim":
        sell_planner(held, saved_prices, funds)
        return

    st.caption(
        "Simulate buying more at a lower price: see your new average, how much the loss shrinks, "
        "and whether the extra cash is doing enough work."
    )
    inp, out = st.columns([1, 2], gap="large")

    with inp:
        sym = st.selectbox("Stock", sorted(held), format_func=companies.display_label, key="dca_symbol")
        p = held[sym]
        saved = saved_prices.get(sym)
        buy_price = st.number_input(
            f"DCA price ({CCY})",
            min_value=0.0, step=0.01, format="%.4f", placeholder="0.00",
            value=float(saved) if saved else None, key=f"dca_price_{sym}",
            help="Price you'd buy at. Gain/loss is measured at this price.",
        )
        mode = st.radio("Size the buy by", ["Shares", f"Amount ({CCY})"], horizontal=True, key="dca_mode")
        if mode == "Shares":
            add_qty = int(
                st.number_input(
                    "Shares to buy", min_value=1, step=1,
                    value=max(1, p.quantity // 4), key=f"dca_qty_{sym}",
                )
            )
        else:
            budget = st.number_input(
                f"Amount ({CCY})", min_value=0.0, step=1000.0, value=10000.0, key=f"dca_amt_{sym}"
            )
            add_qty = int(budget // buy_price) if buy_price else 0
            st.caption(f"≈ {add_qty:,} shares")
        fee = st.number_input(
            f"Fee ({CCY})", min_value=0.0, step=1.0, value=None, placeholder="auto", key="dca_fee",
            help=f"Total brokerage fee for this buy. Empty = {DEFAULT_FEE_RATE * 100:.3f}% of the trade value (0 for funds).",
        )
        st.caption(
            f"Current position: **{p.quantity:,}** shares · broker avg **{price(p.broker_avg)}** · "
            f"break-even **{price(p.breakeven)}**"
        )

    with out:
        if not buy_price:
            st.info("Enter a DCA price to run the simulation.")
            return
        if add_qty < 1:
            st.info("That amount doesn't buy a single share at this price.")
            return

        r = simulate_dca(
            p.quantity, p.broker_avg, p.net_invested, buy_price, add_qty,
            fee if fee else fee_for_trade(add_qty, buy_price, sym in funds),
        )

        def delta_pp(v: float | None) -> str | None:
            return None if v is None else f"{v * 100:+.2f} pp"

        c1, c2, c3 = st.columns(3)
        c1.metric(
            "New broker avg", price(r.new_broker_avg),
            f"{r.new_broker_avg - p.broker_avg:+,.4f}", delta_color="inverse",
        )
        c2.metric(
            "New break-even", price(r.new_breakeven),
            f"{r.new_breakeven - p.breakeven:+,.4f}", delta_color="inverse",
        )
        c3.metric("Cost of this buy", money(r.cost), f"{r.new_qty:,} shares after", delta_color="off")

        c4, c5, c6 = st.columns(3)
        c4.metric("Gain/loss vs broker avg", pct(r.broker_pct_after), delta_pp(r.broker_pct_improvement))
        c5.metric("Gain/loss vs break-even", pct(r.breakeven_pct_after), delta_pp(r.breakeven_pct_improvement))
        rally_before = (p.breakeven - buy_price) / buy_price if p.breakeven and p.breakeven > 0 else None
        rally_after = (r.new_breakeven - buy_price) / buy_price if r.new_breakeven and r.new_breakeven > 0 else None
        c6.metric(
            "Rally needed to break even", pct(rally_after),
            None if rally_before is None or rally_after is None else f"{(rally_after - rally_before) * 100:+.2f} pp",
            delta_color="inverse",
        )

        # Position size: other holdings stay as they are; this one is valued at the DCA price
        # before and after, so the only thing that moves the weight is the extra shares bought.
        others = sum(
            (q.market_value if q.market_value is not None else q.cost_basis_remaining)
            for s_, q in held.items()
            if s_ != sym
        )
        value_before = p.quantity * buy_price
        value_after = r.new_qty * buy_price
        wallet_before = others + value_before
        wallet_after = others + value_after
        share_before = value_before / wallet_before * 100 if wallet_before else 0.0
        share_after = value_after / wallet_after * 100 if wallet_after else 0.0
        cost_basis_before = p.broker_avg * p.quantity
        cost_basis_after = r.new_broker_avg * r.new_qty

        c7, c8, c9 = st.columns(3)
        c7.metric(
            "Position value after", money(value_after), f"{value_after - value_before:+,.0f} {CCY}",
            delta_color="off",
        )
        c8.metric(
            "Total invested after", money(cost_basis_after),
            f"{cost_basis_after - cost_basis_before:+,.0f} {CCY}", delta_color="off",
            help="Cost basis of the shares held (broker average × shares), fees included.",
        )
        c9.metric(
            "Share of wallet", f"{share_after:.1f}%", f"{share_after - share_before:+.1f} pp",
            delta_color="off",
            help="Position value ÷ total value of your open positions, at the DCA price.",
        )
        limit_now = st.session_state.get("pf_limit", 20.0)
        if share_after > limit_now:
            st.warning(
                f"This would make **{sym}** **{share_after:.1f}%** of your wallet, above your "
                f"{limit_now:g}% size limit (set on the Allocation tab)."
            )

        if r.broker_pct_improvement is not None and r.cost > 0:
            per_k = r.broker_pct_improvement * 100 / (r.cost / 1000)
            st.markdown(
                f"**Efficiency:** every 1,000 {CCY} spent here moves your gain/loss vs broker avg by "
                f"**{per_k:+.2f} pp**."
            )

    # Compare several buy sizes side by side to judge diminishing returns.
    st.subheader("Compare buy sizes")
    rows = []
    for mult in (0.1, 0.25, 0.5, 1.0, 2.0):
        q = max(1, round(p.quantity * mult))
        s = simulate_dca(
            p.quantity, p.broker_avg, p.net_invested, buy_price, q,
            fee if fee else fee_for_trade(q, buy_price, sym in funds),
        )
        imp = s.broker_pct_improvement
        rows.append(
            {
                "Buy (% of holding)": f"{mult:.0%}",
                "Shares": q,
                f"Cost ({CCY})": s.cost,
                "New broker avg": s.new_broker_avg,
                "New break-even": s.new_breakeven,
                "Gain/loss vs avg": (s.broker_pct_after or 0) * 100,
                "Improvement (pp)": (imp or 0) * 100,
                "pp per 1,000": (imp * 100 / (s.cost / 1000)) if imp is not None and s.cost else 0.0,
            }
        )
    cmp_df = pd.DataFrame(rows)

    def _tone_css(v: float) -> str:
        return f"color: {_tone(v)}; font-weight: 600"

    st.dataframe(
        cmp_df.style.map(_tone_css, subset=["Gain/loss vs avg", "Improvement (pp)"]).format(
            {
                "Shares": "{:,}",
                f"Cost ({CCY})": "{:,.0f}",
                "New broker avg": "{:,.4f}",
                "New break-even": "{:,.4f}",
                "Gain/loss vs avg": "{:+.2f}%",
                "Improvement (pp)": "{:+.2f}",
                "pp per 1,000": "{:+.2f}",
            }
        ),
        hide_index=True,
        use_container_width=True,
    )


with tab_dca:
    dca_tab(positions, saved_prices, frozenset(fund_symbols))


# --- Performance tab ------------------------------------------------------------
def _yr(x: float | None) -> str:
    """Annualised return as text; absurd short-period annualisations are capped."""
    if x is None:
        return "—"
    return ">+999%" if x > 9.99 else f"{x * 100:+.1f}%"


@st.fragment
def perf_tab(positions, adj_txns, dividends, history, price_entries) -> None:
    today = cairo_today().isoformat()
    first = min(t.date for t in adj_txns)
    days = (date.fromisoformat(today) - date.fromisoformat(first)).days
    held_symbols = sorted({t.symbol for t in adj_txns})

    # Overlay the saved current prices (at the date they were saved) so the curve ends exactly at
    # the value shown on the Portfolio tab, even for prices saved before history was recorded.
    curve_history = {s: dict(pts) for s, pts in history.items()}
    for s, e in price_entries.items():
        curve_history.setdefault(s, {})[str(e["updated_at"])[:10]] = float(e["current_price"])
    curve_history = {s: sorted(d.items()) for s, d in curve_history.items()}

    flows = perf.cash_flows(adj_txns, dividends)
    value_now = perf.terminal_value(positions)
    profit = sum(a for _, a in flows) + value_now
    port_x = perf.xirr_with_terminal(flows, value_now, today)

    head, rate_col, btn_col = st.columns([3, 1.2, 1.4], vertical_alignment="bottom")
    head.caption(
        "Time-aware performance: XIRR weighs *when* each EGP went in or out, so it's comparable "
        "with a benchmark. Dividends and sale proceeds count as money back."
    )
    rate = rate_col.number_input(
        "Deposit / T-bill rate (% / yr)", min_value=0.0, max_value=200.0,
        value=setting_float("deposit_rate", 25.0), step=0.5, key="perf_rate",
        on_change=_save_setting, args=("perf_rate", "deposit_rate"),
        help="Used for the 'same cash in a deposit' comparison. Saved automatically.",
    )
    eodhd_key = (st.session_state.get("tab_eodhd_api_key") or os.getenv("EODHD_API_KEY", "")).strip()
    syms = stock_symbols(held_symbols, fund_symbols) + [EGX30_SYMBOL]
    if btn_col.button(
        "⬇️ Backfill price history", use_container_width=True, disabled=not eodhd_key,
        help=f"Downloads daily closes from EODHD: {len(syms)} API calls (one per stock + EGX30). "
        "The free plan allows 20 calls/day, shared with the price sync. "
        "Uses the key from the Prices tab." if eodhd_key else "Add your EODHD API key on the Prices tab first.",
    ):
        syms = stock_symbols(syms, live_funds())  # re-check against the DB, not the page's copy
        with st.status(f"Downloading history for {len(syms)} symbols…", expanded=False) as status:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda s: (s, _fetch_hist(s, first, eodhd_key)), syms))
            written, failed = 0, []
            for s, (pts, err) in results:
                if pts:
                    written += store.upsert_price_history(s, pts)
                else:
                    failed.append(f"{s} ({err})")
            status.update(label="History updated", state="complete")
        msg = f"Saved {written:,} daily prices."
        if failed:
            msg += f" Failed: {', '.join(failed)}."
        st.session_state["toast"] = msg
        st.rerun()

    idx_hist = history.get(EGX30_SYMBOL, [])
    idx = perf.index_equivalent(flows, idx_hist, today) if idx_hist else None
    dep = perf.deposit_equivalent(flows, rate / 100.0, today)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Your XIRR (per year)", _yr(port_x), f"over {days:,} days", delta_color="off")
    m2.metric("Total profit", money(profit), help="Realized + unrealized + dividends.")
    if idx and idx["xirr"] is not None and port_x is not None:
        m3.metric(
            "You vs EGX30", f"{(port_x - idx['xirr']) * 100:+.1f} pp",
            f"EGX30: {_yr(idx['xirr'])} / yr", delta_color="off",
            help="Same cash flows, same dates, invested in the index instead.",
        )
    else:
        m3.metric("You vs EGX30", "—", "backfill history", delta_color="off")
    if dep and port_x is not None:
        m4.metric(
            "You vs deposit", f"{(port_x - dep['xirr']) * 100:+.1f} pp",
            f"Deposit: {_yr(dep['xirr'])} / yr", delta_color="off",
        )
    else:
        m4.metric("You vs deposit", "—")

    if days < 90:
        st.caption("⚠️ Under 90 days of history: annualised figures swing wildly and shouldn't be trusted yet.")
    if idx and idx_hist and idx_hist[0][0] > first:
        st.caption(f"⚠️ EGX30 history starts {idx_hist[0][0]}, after your first trade ({first}); the benchmark is approximate.")
    if idx:
        st.markdown(
            f"**Same cash in EGX30** would be worth **{money(idx['value'])}** today vs your holdings at "
            f"**{money(value_now)}** (**{value_now - idx['value']:+,.0f} {CCY}**). "
            f"**Same cash in a {rate:g}% deposit:** {money(dep['value'])} "
            f"(**{value_now - dep['value']:+,.0f} {CCY}**)."
        )

    # --- equity curve -------------------------------------------------------------
    curve = perf.equity_curve(adj_txns, curve_history, end=today, dividends=dividends)
    df = pd.DataFrame(curve)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    stamps = [str(e["updated_at"])[:10] for e in price_entries.values() if e.get("updated_at")]
    components.html(
        card_html(df, max(stamps) if stamps else today, CCY, index_history=curve_history.get(EGX30_SYMBOL)),
        height=CARD_HEIGHT,
    )
    priced_days = len({d for s in held_symbols for d, _ in history.get(s, [])})
    st.caption(
        f"Portfolio value includes dividends received; gap to net deposits = total profit. Built from {priced_days:,} days of saved "
        "prices; days without a saved price use your last trade price. History uses raw (unadjusted) EODHD "
        "closes, matching the share counts in your ledger."
    )

    # --- per stock ------------------------------------------------------------------
    st.subheader("By stock")
    by_symbol = {p.symbol: p for p in positions}
    rows = []
    for s in sorted(by_symbol):
        p = by_symbol[s]
        fl = perf.cash_flows(adj_txns, dividends, s)
        val = perf.terminal_value(positions, s)
        prof = sum(a for _, a in fl) + val
        d = perf.holding_days(adj_txns, s, today)
        x = perf.xirr_with_terminal(fl, val, today) if d >= 30 else None
        rows.append(
            {
                "Logo": logo_cell(s),
                "Symbol": s,
                "Status": "Open" if p.quantity > 0 else "Closed",
                "Days": d,
                f"Profit ({CCY})": prof,
                "Return %": prof / p.total_buy_cost * 100 if p.total_buy_cost > 0 else None,
                "XIRR / yr": x * 100 if x is not None else None,
            }
        )
    tbl = pd.DataFrame(rows).sort_values("Return %", ascending=False, na_position="last")
    pc = f"Profit ({CCY})"

    def _fmt_pct(v):
        return "—" if v is None or v != v else (">+999%" if v > 999 else f"{v:+.1f}%")

    st.dataframe(
        tbl.style.map(lambda v: f"color: {_tone(v if v == v else None)}; font-weight: 600",
                      subset=[pc, "Return %", "XIRR / yr"])
        .format({pc: lambda v: _signed(v), "Return %": _fmt_pct, "XIRR / yr": _fmt_pct, "Days": "{:,}"}),
        hide_index=True,
        use_container_width=True,
        column_config={"Logo": st.column_config.ImageColumn(" ", width="small")},
    )
    st.caption("XIRR is shown only for stocks held 30+ days. Return % = profit ÷ total money paid in.")


with tab_perf:
    perf_tab(positions, adj_txns, all_dividends_obj, price_history, saved_price_entries)


# --- Net Worth tab --------------------------------------------------------------
_NW_CAT_LABELS = {
    CAT_US_STOCK: "🌎 US / Employer Stock (e.g. PCOR / ESPP)",
    CAT_FUND: "📊 Mutual Fund (Money Market / Fixed Income / Savings)",
    CAT_GOLD: "🪙 Gold (Bars / Pounds)",
    CAT_FX: "💵 Foreign Currency (Cash)",
    CAT_CD: "📜 Certificate of Deposit (CD — EGP or Foreign Currency)",
    CAT_CASH: "🏦 Cash / Savings Account",
}
_NW_CAT_SHORT = {
    CAT_US_STOCK: "🌎 US Stock",
    CAT_FUND: "📊 Fund",
    CAT_GOLD: "🪙 Gold",
    CAT_FX: "💵 FX",
    CAT_CD: "📜 CD",
    CAT_CASH: "🏦 Cash",
}


@st.dialog("🏦 Net Worth Item", width="large")
def net_worth_dialog(
    item: dict | None, nonce: int, price_24k: float, price_21k: float, usd_egp_rate: float
) -> None:
    """Add (item=None) or edit a non-stock asset (US Stock, Mutual Fund, Gold, FX, CD, or Cash)."""
    editing = item is not None
    p = f"nw{nonce}_"
    cats = [CAT_US_STOCK, CAT_FUND, CAT_GOLD, CAT_FX, CAT_CD, CAT_CASH]
    default_cat = item["category"] if editing and item["category"] in cats else CAT_US_STOCK
    category = st.selectbox(
        "Asset category",
        cats,
        index=cats.index(default_cat),
        format_func=lambda c: _NW_CAT_LABELS[c],
        key=f"{p}cat",
    )

    p24, p21 = effective_gold_prices(price_24k, price_21k)
    karat = 24
    grams = 0.0
    unit_value = 0.0
    currency = "EGP"
    fx_rate = 1.0

    if category == CAT_US_STOCK:
        currency = "USD"
        name = st.text_input(
            "Ticker / Stock label",
            value=item["name"] if editing else "PCOR",
            placeholder="e.g. PCOR or PCOR (ESPP)",
            key=f"{p}us_name",
        ).strip()
        c1, c2, c3 = st.columns([1.2, 1.2, 1.2])
        quantity = float(
            c1.number_input(
                "Shares held",
                min_value=0.0,
                step=1.0,
                format="%.4f",
                value=float(item["quantity"]) if editing else 0.0,
                key=f"{p}us_qty",
            )
        )
        unit_value = float(
            c2.number_input(
                "Current share price ($ USD)",
                min_value=0.0,
                step=0.5,
                format="%.2f",
                value=float(item["unit_value"]) if editing else 0.0,
                key=f"{p}us_px",
            )
        )
        default_fx = (
            float(usd_egp_rate)
            if usd_egp_rate > 0
            else (float(item.get("fx_rate") or 50.0) if editing and float(item.get("fx_rate") or 0) > 1.0 else 50.0)
        )
        fx_rate = float(
            c3.number_input(
                f"USD Rate ({CCY} per $1)",
                min_value=0.0,
                step=0.1,
                format="%.4f",
                value=default_fx,
                key=f"{p}us_fx_rate",
            )
        )
        usd_val = quantity * unit_value
        st.caption(f"Value: **${usd_val:,.2f} USD** → **{usd_val * fx_rate:,.2f} {CCY}**")

    elif category == CAT_FUND:
        name_col, ccy_col = st.columns([2.2, 1])
        name = name_col.text_input(
            "Mutual fund name",
            value=item["name"] if editing else "",
            placeholder="e.g. Beltone B-Secure (BMM) / Fawry Yawmy",
            key=f"{p}fund_name",
        ).strip()
        fund_curr_opts = list(CD_CURRENCIES) + [OTHER]
        existing_ccy = (item.get("currency") or "EGP").upper() if editing else "EGP"
        default_fund_ccy = existing_ccy if existing_ccy in CD_CURRENCIES else OTHER
        picked_ccy = ccy_col.selectbox(
            "Currency",
            fund_curr_opts,
            index=fund_curr_opts.index(default_fund_ccy),
            format_func=lambda s: "Other…" if s == OTHER else s,
            key=f"{p}fund_ccy",
        )
        if picked_ccy == OTHER:
            currency = st.text_input(
                "Currency code",
                value=existing_ccy if editing and existing_ccy not in CD_CURRENCIES else "",
                placeholder="e.g. CAD",
                key=f"{p}fund_ccy_custom",
            ).strip().upper() or "EGP"
        else:
            currency = picked_ccy

        modes = ["Total Value / Balance", "Units × NAV Price"]
        default_mode = 1 if (editing and float(item["unit_value"]) > 0) else 0
        entry_mode = st.radio(
            "How to enter balance",
            modes,
            index=default_mode,
            horizontal=True,
            key=f"{p}fund_mode",
        )

        if currency != "EGP":
            default_fx = (
                float(usd_egp_rate)
                if currency == "USD" and usd_egp_rate > 0
                else (float(item.get("fx_rate") or 50.0) if editing and float(item.get("fx_rate") or 0) > 1.0 else 50.0)
            )
        else:
            default_fx = 1.0

        if entry_mode == "Total Value / Balance":
            if currency == "EGP":
                c1, c_rate = st.columns(2)
                fx_rate = 1.0
            else:
                c1, c_rate, c_fx = st.columns([1.4, 1.1, 1.3])
                fx_rate = float(
                    c_fx.number_input(
                        f"Rate ({CCY} per 1 {currency})",
                        min_value=0.0,
                        step=0.1,
                        format="%.4f",
                        value=default_fx,
                        key=f"{p}fund_fx_rate",
                    )
                )
            quantity = float(
                c1.number_input(
                    f"Current total value ({currency})",
                    min_value=0.0,
                    step=1000.0 if currency == "EGP" else 100.0,
                    format="%.2f",
                    value=float(item["quantity"] * (item["unit_value"] or 1.0)) if editing else 0.0,
                    key=f"{p}fund_total",
                )
            )
            grams = float(
                c_rate.number_input(
                    "Avg expected rate (% / yr, optional)",
                    min_value=0.0,
                    max_value=200.0,
                    step=0.25,
                    format="%.2f",
                    value=float(item.get("grams") or 0.0) if editing else 20.0,
                    key=f"{p}fund_yield",
                )
            )
            unit_value = 0.0
            native_val = quantity
        else:
            if currency == "EGP":
                c1, c2, c_rate = st.columns([1.2, 1.2, 1.2])
                fx_rate = 1.0
            else:
                c1, c2, c_rate, c3 = st.columns([1.1, 1.1, 1.1, 1.1])
                fx_rate = float(
                    c3.number_input(
                        f"Rate ({CCY} per 1 {currency})",
                        min_value=0.0,
                        step=0.1,
                        format="%.4f",
                        value=default_fx,
                        key=f"{p}fund_fx_rate",
                    )
                )
            quantity = float(
                c1.number_input(
                    "Units held",
                    min_value=0.0,
                    step=10.0,
                    format="%.4f",
                    value=float(item["quantity"]) if editing else 0.0,
                    key=f"{p}fund_units",
                )
            )
            unit_value = float(
                c2.number_input(
                    f"Current NAV / unit price ({currency})",
                    min_value=0.0,
                    step=0.01,
                    format="%.4f",
                    value=float(item["unit_value"]) if editing and float(item["unit_value"]) > 0 else 1.0,
                    key=f"{p}fund_nav",
                )
            )
            grams = float(
                c_rate.number_input(
                    "Avg expected rate (% / yr, optional)",
                    min_value=0.0,
                    max_value=200.0,
                    step=0.25,
                    format="%.2f",
                    value=float(item.get("grams") or 0.0) if editing else 20.0,
                    key=f"{p}fund_yield",
                )
            )
            native_val = quantity * unit_value

        val_egp = native_val * fx_rate
        cap_parts = [
            f"Value: **{native_val:,.2f} {currency}**"
            + (f" → **{val_egp:,.2f} {CCY}**" if currency != "EGP" else "")
        ]
        if native_val > 0 and grams > 0:
            yr_native = native_val * (grams / 100.0)
            yr_egp = yr_native * fx_rate
            if currency != "EGP":
                cap_parts.append(
                    f"Expected return: **~{yr_native:,.2f} {currency}/yr** "
                    f"(**~{yr_egp:,.0f} {CCY}/yr** · **~{yr_egp / 12.0:,.0f} {CCY}/mo**)"
                )
            else:
                cap_parts.append(
                    f"Expected return: **~{yr_egp:,.0f} {CCY}/yr** (**~{yr_egp / 12.0:,.0f} {CCY}/mo**)"
                )
        st.caption(" · ".join(cap_parts))

    elif category == CAT_GOLD:
        preset_names = list(GOLD_PRESETS.keys()) + [OTHER]
        default_preset = item["name"] if editing and item["name"] in GOLD_PRESETS else (OTHER if editing else preset_names[0])
        chosen_preset = st.selectbox(
            "Gold item",
            preset_names,
            index=preset_names.index(default_preset),
            format_func=lambda s: "Custom gold item…" if s == OTHER else s,
            key=f"{p}gold_preset",
        )
        if chosen_preset == OTHER:
            c1, c2, c3 = st.columns([2, 1, 1])
            name = c1.text_input(
                "Item name",
                value=item["name"] if editing else "",
                placeholder="e.g. 50g BTC Bar",
                key=f"{p}gold_name",
            ).strip()
            karat_opts = [24, 21]
            default_k = int(item["karat"]) if editing and int(item["karat"]) in karat_opts else 24
            karat = c2.selectbox(
                "Karat",
                karat_opts,
                index=karat_opts.index(default_k),
                format_func=lambda k: f"{k}K",
                key=f"{p}gold_karat",
            )
            grams = float(
                c3.number_input(
                    "Weight per piece (g)",
                    min_value=0.01,
                    step=0.5,
                    format="%.2f",
                    value=float(item["grams"]) if editing and float(item["grams"]) > 0 else 8.0,
                    key=f"{p}gold_grams",
                )
            )
        else:
            name = chosen_preset
            karat, grams = GOLD_PRESETS[chosen_preset]

        c_qty, c_cb = st.columns(2)
        quantity = float(
            c_qty.number_input(
                "Quantity (pieces)",
                min_value=0.0,
                step=1.0,
                format="%.2f",
                value=float(item["quantity"]) if editing else 1.0,
                key=f"{p}gold_qty",
            )
        )
        unit_value = float(
            c_cb.number_input(
                f"Cashback / premium on resale ({CCY} / gram, optional)",
                min_value=0.0,
                step=5.0,
                format="%.2f",
                value=float(item["unit_value"]) if editing else 0.0,
                help="Extra EGP/gram refunded on resale above the raw gold price (leave 0 for pure bullion value).",
                key=f"{p}gold_cb",
            )
        )
        active_px = p21 if karat == 21 else p24
        est_val = quantity * grams * (active_px + unit_value)
        st.caption(
            f"Weight: **{quantity * grams:,.2f}g ({karat}K)** · "
            f"Active {karat}K price: **{active_px:,.2f} {CCY}/g** → "
            f"Value: **{est_val:,.2f} {CCY}**"
        )

    elif category == CAT_FX:
        curr_opts = list(COMMON_CURRENCIES) + [OTHER]
        existing_ccy = (item.get("currency") if editing else None) or (item["name"] if editing else "USD")
        default_curr = existing_ccy if existing_ccy in COMMON_CURRENCIES else (OTHER if editing else "USD")
        c1, c2, c3 = st.columns([1.2, 1.4, 1.4])
        picked_curr = c1.selectbox(
            "Currency",
            curr_opts,
            index=curr_opts.index(default_curr),
            format_func=lambda s: "Other…" if s == OTHER else s,
            key=f"{p}fx_pick",
        )
        if picked_curr == OTHER:
            currency = st.text_input(
                "Currency code",
                value=existing_ccy if editing else "",
                placeholder="e.g. CAD",
                key=f"{p}fx_custom",
            ).strip().upper()
        else:
            currency = picked_curr
        name = currency
        quantity = float(
            c2.number_input(
                f"Amount ({currency or 'FX'})",
                min_value=0.0,
                step=100.0,
                format="%.2f",
                value=float(item["quantity"]) if editing else 0.0,
                key=f"{p}fx_qty",
            )
        )
        default_rate = (
            float(usd_egp_rate)
            if currency == "USD" and usd_egp_rate > 0
            else (
                float(item.get("fx_rate") or item["unit_value"])
                if editing and float(item.get("fx_rate") or item["unit_value"]) > 1.0
                else 50.0
            )
        )
        fx_rate = float(
            c3.number_input(
                f"Exchange rate ({CCY} per 1 {currency or 'unit'})",
                min_value=0.0,
                step=0.1,
                format="%.4f",
                value=default_rate,
                key=f"{p}fx_rate",
            )
        )
        unit_value = fx_rate
        st.caption(f"Value in {CCY}: **{quantity * fx_rate:,.2f} {CCY}**")

    else:
        is_cd = category == CAT_CD
        name_col, ccy_col = st.columns([2.2, 1])
        name = name_col.text_input(
            "Certificate name / Bank" if is_cd else "Account / Cash label",
            value=item["name"] if editing else "",
            placeholder="e.g. NBE USD CD / Banque Misr 27% CD" if is_cd else "e.g. CIB Savings / Thndr Cash",
            key=f"{p}cd_name",
        ).strip()
        cd_curr_opts = list(CD_CURRENCIES) + [OTHER]
        existing_ccy = (item.get("currency") or "EGP").upper() if editing else "EGP"
        default_cd_ccy = existing_ccy if existing_ccy in CD_CURRENCIES else OTHER
        picked_ccy = ccy_col.selectbox(
            "Currency",
            cd_curr_opts,
            index=cd_curr_opts.index(default_cd_ccy),
            format_func=lambda s: "Other…" if s == OTHER else s,
            key=f"{p}cd_ccy",
        )
        if picked_ccy == OTHER:
            currency = st.text_input(
                "Currency code",
                value=existing_ccy if editing and existing_ccy not in CD_CURRENCIES else "",
                placeholder="e.g. CAD",
                key=f"{p}cd_ccy_custom",
            ).strip().upper() or "EGP"
        else:
            currency = picked_ccy

        if currency == "EGP":
            c1, c2 = st.columns(2)
            fx_rate = 1.0
        else:
            c1, c2, c3 = st.columns([1.4, 1.1, 1.3])
            default_fx = (
                float(usd_egp_rate)
                if currency == "USD" and usd_egp_rate > 0
                else (float(item.get("fx_rate") or 50.0) if editing and float(item.get("fx_rate") or 0) > 1.0 else 50.0)
            )
            fx_rate = float(
                c3.number_input(
                    f"Rate ({CCY} per 1 {currency})",
                    min_value=0.0,
                    step=0.1,
                    format="%.4f",
                    value=default_fx,
                    key=f"{p}cd_fx_rate",
                )
            )

        quantity = float(
            c1.number_input(
                f"{'Principal amount' if is_cd else 'Balance'} ({currency})",
                min_value=0.0,
                step=1000.0 if currency == "EGP" else 100.0,
                format="%.2f",
                value=float(item["quantity"]) if editing else 0.0,
                key=f"{p}cd_qty",
            )
        )
        default_yield = 5.5 if (is_cd and currency != "EGP") else (27.0 if is_cd else 0.0)
        unit_value = float(
            c2.number_input(
                "Interest rate (% / yr)",
                min_value=0.0,
                max_value=200.0,
                step=0.25,
                format="%.2f",
                value=float(item["unit_value"]) if editing else default_yield,
                key=f"{p}cd_rate",
            )
        )
        val_egp = quantity * fx_rate
        if quantity > 0:
            parts = [f"Value in {CCY}: **{val_egp:,.2f} {CCY}**"]
            if unit_value > 0:
                yr_native = quantity * (unit_value / 100.0)
                yr_egp = yr_native * fx_rate
                if currency != "EGP":
                    parts.append(
                        f"Payout: **{yr_native:,.2f} {currency}/yr** "
                        f"(**{yr_egp:,.0f} {CCY}/yr** · **{yr_egp / 12.0:,.0f} {CCY}/mo**)"
                    )
                else:
                    parts.append(f"Payout: **{yr_egp:,.0f} {CCY}/yr** (**{yr_egp / 12.0:,.0f} {CCY}/mo**)")
            st.caption(" · ".join(parts))

    notes = st.text_input(
        "Notes (optional)",
        value=item["notes"] if editing else "",
        placeholder="e.g. Matures Jan 2027, held at CIB, BTC bar…",
        key=f"{p}notes",
    ).strip()

    st.write("")
    if st.button(
        "Save changes" if editing else "Add to Net Worth",
        type="primary",
        use_container_width=True,
        key=f"{p}save",
    ):
        if not name:
            st.error("Please provide a name or select an item.")
        elif quantity <= 0:
            st.error("Quantity / amount must be greater than zero.")
        else:
            if currency == "USD" and fx_rate > 0 and abs(fx_rate - usd_egp_rate) > 1e-6:
                store.set_setting("usd_egp_rate", fx_rate)
            if editing:
                store.update_net_worth_item(
                    int(item["id"]), category, name, quantity, unit_value, karat, grams, notes, currency, fx_rate
                )
                record_current_snapshot()
                st.session_state["toast"] = f"Updated {name}."
            else:
                store.add_net_worth_item(
                    category, name, quantity, unit_value, karat, grams, notes, currency, fx_rate
                )
                record_current_snapshot()
                st.session_state["toast"] = f"Added {name} to Net Worth."
            st.rerun()


@st.dialog("📸 Net Worth Snapshot / Backfill", width="large")
def nw_snapshot_dialog(
    nonce: int,
    live_nw: dict,
    eq_by_date: dict[str, float],
    saved_by_date: dict[str, dict],
) -> None:
    """Save today's Net Worth snapshot or backfill/edit any historical date."""
    p = f"nwsnap{nonce}_"
    today_d = cairo_today()
    today_iso = today_d.isoformat()

    snap_date = st.date_input(
        "Snapshot date",
        value=today_d,
        max_value=today_d,
        key=f"{p}date",
        help="Pick today to record your current live Net Worth, or pick any past date (e.g. end of last month) to backfill.",
    )
    iso = snap_date.isoformat()

    live_snap = snapshot_from_nw(iso, live_nw)
    existing = saved_by_date.get(iso)

    if existing is not None:
        defaults = existing
        st.caption(
            f"📌 **Saved snapshot found for `{iso}`.** You can review or edit its breakdown below, then click **Save Snapshot** to overwrite."
        )
    elif iso == today_iso:
        defaults = live_snap
        st.caption(
            f"✨ **Today (`{iso}`):** pre-filled with your current live Net Worth across all 6 asset classes."
        )
    else:
        # Backfilling a historical date: look up historical EGX portfolio value on or before `iso`
        hist_eq = eq_by_date.get(iso)
        if hist_eq is None and eq_by_date:
            earlier = [d for d in eq_by_date if d <= iso]
            hist_eq = eq_by_date[max(earlier)] if earlier else 0.0
        defaults = dict(live_snap)
        defaults["stocks_egp"] = round(float(hist_eq or 0.0), 2)
        st.caption(
            f"🕰 **Backfilling `{iso}`:** **EGX Equities** auto-filled from your portfolio history on `{iso}` "
            f"(**{defaults['stocks_egp']:,.0f} {CCY}**). Other slices are pre-filled with your current holdings — adjust any that differed on `{iso}`."
        )

    c1, c2 = st.columns(2)
    stocks_v = float(
        c1.number_input(
            f"📈 EGX Equities — Stocks & Stock Funds ({CCY})",
            min_value=0.0,
            step=1000.0,
            format="%.2f",
            value=float(defaults.get("stocks_egp", 0.0)),
            key=f"{p}{iso}_stocks",
        )
    )
    us_stocks_v = float(
        c2.number_input(
            f"🌎 US / Employer Stock — e.g. PCOR ({CCY})",
            min_value=0.0,
            step=1000.0,
            format="%.2f",
            value=float(defaults.get("us_stocks_egp", 0.0)),
            key=f"{p}{iso}_us_stocks",
        )
    )
    c3, c4 = st.columns(2)
    funds_v = float(
        c3.number_input(
            f"📊 Mutual Funds ({CCY})",
            min_value=0.0,
            step=1000.0,
            format="%.2f",
            value=float(defaults.get("funds_egp", 0.0)),
            key=f"{p}{iso}_funds",
        )
    )
    gold_v = float(
        c4.number_input(
            f"🪙 Gold ({CCY})",
            min_value=0.0,
            step=1000.0,
            format="%.2f",
            value=float(defaults.get("gold_egp", 0.0)),
            key=f"{p}{iso}_gold",
        )
    )
    c5, c6 = st.columns(2)
    fx_v = float(
        c5.number_input(
            f"💵 Foreign Currency Cash ({CCY})",
            min_value=0.0,
            step=1000.0,
            format="%.2f",
            value=float(defaults.get("fx_egp", 0.0)),
            key=f"{p}{iso}_fx",
        )
    )
    cd_cash_v = float(
        c6.number_input(
            f"🏦 CDs & Cash ({CCY})",
            min_value=0.0,
            step=1000.0,
            format="%.2f",
            value=float(defaults.get("cd_cash_egp", 0.0)),
            key=f"{p}{iso}_cd_cash",
        )
    )
    total_v = round(stocks_v + us_stocks_v + funds_v + gold_v + fx_v + cd_cash_v, 2)
    edited_slices = {
        "stocks_egp": stocks_v, "us_stocks_egp": us_stocks_v, "funds_egp": funds_v,
        "gold_egp": gold_v, "fx_egp": fx_v, "cd_cash_egp": cd_cash_v,
    }
    auto_usd_v = snapshot_usd_exposure(edited_slices, defaults, live_nw)
    override_usd = st.checkbox(
        "Override USD exposure manually",
        value=False,
        key=f"{p}{iso}_usd_override",
        help="By default, USD exposure is recalculated from the slices above (it moves with the US Stock, "
             "Mutual Funds, FX and CDs values you enter). Tick to type an exact figure instead.",
    )
    if override_usd:
        usd_total_v = float(
            st.number_input(
                f"🇺🇸 Total USD Exposure included above (in {CCY})",
                min_value=0.0,
                step=1000.0,
                format="%.2f",
                value=auto_usd_v,
                key=f"{p}{iso}_usd_total",
            )
        )
    else:
        usd_total_v = auto_usd_v
    usd_share = (usd_total_v / total_v * 100.0) if total_v > 0 else 0.0
    st.caption(f"🇺🇸 USD exposure: **{usd_total_v:,.0f} {CCY}** ({usd_share:.1f}% of total)")
    st.markdown(f"**Snapshot Total on `{iso}`:** `{total_v:,.2f} {CCY}`")

    if st.button(
        f"💾 Save Snapshot for {iso}",
        type="primary",
        use_container_width=True,
        key=f"{p}save",
    ):
        if total_v <= 0:
            st.error("Total Net Worth must be greater than zero.")
        else:
            store.upsert_net_worth_snapshot(
                {
                    "date": iso,
                    "total_egp": total_v,
                    "stocks_egp": stocks_v,
                    "us_stocks_egp": us_stocks_v,
                    "funds_egp": funds_v,
                    "gold_egp": gold_v,
                    "fx_egp": fx_v,
                    "cd_cash_egp": cd_cash_v,
                    "usd_total_egp": usd_total_v,
                }
            )
            st.session_state["toast"] = f"Saved Net Worth snapshot for {iso} ({total_v:,.0f} {CCY})."
            st.rerun()


def _nw_bars_html(nw: dict, meta_by_key: dict[str, str]) -> str:
    """Horizontal allocation bars for each Net Worth slice + Currency/USD Exposure bar."""
    max_pct = max((s["pct"] for s in nw["slices"]), default=1.0) or 1.0
    rows = []
    for s in nw["slices"]:
        fill = min(s["pct"] / max_pct * 100.0, 100.0) if max_pct > 0 else 0.0
        sub = meta_by_key.get(s["key"], "")
        rows.append(
            f"<div class='al-row' style='grid-template-columns:175px 1fr 64px 200px;border-left-color:{s['color']}'>"
            f"<div class='al-id'><span style='display:inline-block;width:10px;height:10px;border-radius:50%;background:{s['color']}'></span>"
            f"{_esc(s['label'])}</div>"
            f"<div class='al-track'><div class='al-fill' style='width:{fill:.1f}%;background:{s['color']}'></div></div>"
            f"<div class='al-w'>{s['pct']:.1f}%</div>"
            f"<div class='al-meta'><strong style='color:#e6e6e6'>{egx_money(s['value'])}</strong>"
            + (f"<br>{_esc(sub)}" if sub else "")
            + "</div></div>"
        )

    # Currency & Hedge Exposure summary card (shows exact % of Net Worth in EGP vs USD vs Gold)
    usd_parts = []
    if nw.get("usd_stock_usd", 0) > 0:
        usd_parts.append(f"${nw['usd_stock_usd']:,.0f} US stock")
    if nw["usd_cash_usd"] > 0:
        usd_parts.append(f"${nw['usd_cash_usd']:,.0f} cash")
    if nw["usd_cd_usd"] > 0:
        usd_parts.append(f"${nw['usd_cd_usd']:,.0f} in CDs")
    if nw.get("usd_fund_usd", 0) > 0:
        usd_parts.append(f"${nw['usd_fund_usd']:,.0f} in funds")
    usd_detail = (
        f"${nw['usd_total_usd']:,.2f} total (" + " · ".join(usd_parts) + ")"
        if nw["usd_total_usd"] > 0
        else "No USD held"
    )
    exposure_items = [
        ("🇪🇬 EGP Assets", nw["egp_pct"], nw["egp_exposure_egp"], "#2ecc71", "Equities, EGP Funds & EGP CDs/Cash"),
        ("🇺🇸 USD Exposure", nw["usd_pct"], nw["usd_total_egp"], "#3498db", usd_detail),
        ("🪙 Gold Exposure", nw["gold_pct"], nw["by_key"]["gold"], "#f1c40f", f"{nw['gold_total_grams']:,.1f}g total"),
    ]
    if nw["other_fx_egp"] > 0:
        exposure_items.append(
            ("🌍 Other FX", nw["other_fx_pct"], nw["other_fx_egp"], "#9b59b6", "Non-USD foreign currency")
        )

    seg_bars = []
    pills = []
    for label, pct_val, egp_val, color, detail in exposure_items:
        if pct_val > 0:
            seg_bars.append(f"<div style='width:{pct_val:.2f}%;background:{color};height:10px'></div>")
        pills.append(
            "<div style='flex:1;min-width:140px;background:#161920;border:1px solid #262a33;"
            f"border-left:3px solid {color};border-radius:8px;padding:8px 10px'>"
            f"<div style='font-size:.78rem;color:#9aa0a6'>{_esc(label)}</div>"
            f"<div style='font-weight:700;font-size:1rem;color:#fff'>{pct_val:.1f}% · {_esc(egx_money(egp_val))}</div>"
            f"<div style='font-size:.73rem;color:#8a8f98'>{_esc(detail)}</div>"
            "</div>"
        )

    exposure_card = (
        "<div class='pf-card' style='margin-top:10px;padding:12px 14px'>"
        "<div style='font-size:.78rem;color:#9aa0a6;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px'>"
        "Currency &amp; Hedge Exposure (% of Net Worth)</div>"
        "<div style='display:flex;width:100%;height:10px;background:#262a33;border-radius:6px;overflow:hidden;margin-bottom:10px'>"
        + "".join(seg_bars)
        + "</div>"
        + "<div style='display:flex;gap:10px;flex-wrap:wrap'>"
        + "".join(pills)
        + "</div></div>"
    )
    return "".join(rows) + exposure_card


@st.fragment
def net_worth_tab(
    positions,
    funds: set,
    nw_rows: list[dict],
    nw_snapshots: list[dict],
    adj_txns,
    history: dict,
    price_entries: dict,
) -> None:
    st.markdown(PORTFOLIO_CSS, unsafe_allow_html=True)
    items = [
        NetWorthItem(
            id=int(r["id"]),
            category=r["category"],
            name=r["name"],
            quantity=float(r["quantity"]),
            unit_value=float(r["unit_value"]),
            karat=int(r["karat"]),
            grams=float(r["grams"]),
            currency=str(r.get("currency") or "EGP"),
            fx_rate=float(r.get("fx_rate") if r.get("fx_rate") is not None else 1.0),
            notes=r["notes"],
        )
        for r in nw_rows
    ]

    st.caption(
        "Complete picture of your net worth in **EGP**, plus your exact **USD % exposure** across both cash and certificates. "
        "**Stocks (EGX)** and **Mutual Funds** sync automatically from your portfolio."
    )

    # --- Gold gram prices + USD/EGP rate + Action buttons ------------------------------
    g1, g2, g3, g4, g5 = st.columns([1.05, 1.05, 1.05, 1.15, 1.05], vertical_alignment="bottom")
    raw_24k = g1.number_input(
        f"🪙 24K Gold ({CCY} / g)",
        min_value=0.0,
        step=10.0,
        format="%.2f",
        value=setting_float("gold_24k_price", 0.0),
        key="nw_gold_24k",
        on_change=_save_nw_setting,
        args=("nw_gold_24k", "gold_24k_price"),
        help="Used to value 24K gold bars. If 21K is left at 0, 21K is auto-calculated as 24K × 21/24.",
    )
    raw_21k = g2.number_input(
        f"🪙 21K Gold ({CCY} / g)",
        min_value=0.0,
        step=10.0,
        format="%.2f",
        value=setting_float("gold_21k_price", 0.0),
        key="nw_gold_21k",
        on_change=_save_nw_setting,
        args=("nw_gold_21k", "gold_21k_price"),
        help="Used to value 21K Gold Pounds. Leave at 0 to auto-derive from 24K (or enter only 21K to derive 24K).",
    )
    usd_rate = g3.number_input(
        f"💵 USD Rate ({CCY} / $1)",
        min_value=0.0,
        step=0.25,
        format="%.2f",
        value=setting_float("usd_egp_rate", 0.0),
        key="nw_usd_rate",
        on_change=_save_nw_setting,
        args=("nw_usd_rate", "usd_egp_rate"),
        help="Exchange rate applied to all USD cash balances and USD Certificates of Deposit. Saved automatically.",
    )
    p24, p21 = effective_gold_prices(raw_24k, raw_21k)
    nw = compute_net_worth(positions, funds, items, p24, p21, usd_rate)
    today_iso = cairo_today().isoformat()

    if g4.button("➕ Add Net Worth Item", type="primary", use_container_width=True, key="nw_add_btn"):
        net_worth_dialog(None, _new_dialog_nonce(), p24, p21, usd_rate)
    if g5.button(
        "📸 Snapshot / Backfill",
        use_container_width=True,
        key="nw_snap_btn",
        help="Save today's Net Worth snapshot or backfill/edit any historical date.",
    ):
        curve_history = {s: dict(pts) for s, pts in history.items()}
        for s, e in price_entries.items():
            curve_history.setdefault(s, {})[str(e["updated_at"])[:10]] = float(e["current_price"])
        curve_history_sorted = {s: sorted(d.items()) for s, d in curve_history.items()}
        eq_curve = perf.equity_curve(adj_txns, curve_history_sorted, end=today_iso)
        eq_by_date = {pt["date"]: float(pt["value"]) for pt in eq_curve}
        saved_by_date = {str(r["date"])[:10]: dict(r) for r in nw_snapshots}
        nw_snapshot_dialog(_new_dialog_nonce(), nw, eq_by_date, saved_by_date)

    if (raw_24k > 0 and raw_21k == 0) or (raw_21k > 0 and raw_24k == 0):
        st.caption(
            f"Active gold prices: **24K = {p24:,.2f} {CCY}/g** · **21K = {p21:,.2f} {CCY}/g** *(auto-derived by karat ratio)*"
        )

    by_k = nw["by_key"]
    tot = nw["total"] or 1.0
    equities_val = by_k["stocks"]
    cd_val = by_k["cd_cash"]

    # --- Top Hero Metrics -------------------------------------------------------------
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Net Worth", money(nw["total"]), f"{len(items)} manual item(s)", delta_color="off")
    m2.metric(
        "Equities (Stocks & Funds)",
        money(equities_val),
        f"{equities_val / tot * 100:.1f}% of net worth" if nw["total"] > 0 else None,
        delta_color="off",
        help="Open EGX stocks and stock funds (such as NBE05) synced from your Portfolio tab.",
    )
    usd_delta = (
        f"{nw['usd_pct']:.1f}% of net worth · ${nw['usd_total_usd']:,.0f} USD"
        if nw["usd_total_usd"] > 0
        else ("0.0% of net worth" if nw["total"] > 0 else None)
    )
    m3.metric(
        "Total USD Exposure",
        money(nw["usd_total_egp"]),
        usd_delta,
        delta_color="off",
        help="Includes liquid USD cash, USD Certificates of Deposit, and USD Mutual Funds, converted to EGP.",
    )
    cd_delta = (
        f"+{nw['annual_cd_income'] / 12.0:,.0f} {CCY}/mo yield"
        if nw["annual_cd_income"] > 0
        else (f"{cd_val / tot * 100:.1f}% of net worth" if nw["total"] > 0 else None)
    )
    m4.metric("CDs & Cash (Total)", money(cd_val), cd_delta, delta_color="off")

    # --- Donut Chart + Allocation & Currency Breakdown --------------------------------
    open_stocks = sum(1 for p in positions if p.quantity > 0 and p.symbol not in funds)
    open_stock_funds = sum(1 for p in positions if p.quantity > 0 and p.symbol in funds)
    us_stock_items = [it for it in items if it.category == CAT_US_STOCK]
    us_shares_total = sum(it.quantity for it in us_stock_items)
    nw_fund_count = sum(1 for it in items if it.category == CAT_FUND)
    fx_count = sum(1 for it in items if it.category == CAT_FX)
    cd_parts = []
    if nw["usd_cd_usd"] > 0:
        cd_parts.append(f"incl. ${nw['usd_cd_usd']:,.0f} USD in CDs")
    if nw["annual_cd_income"] > 0:
        cd_parts.append(f"yields {nw['annual_cd_income'] / 12.0:,.0f} {CCY}/mo")
    eq_parts = [f"{open_stocks} stock(s)"]
    if open_stock_funds > 0:
        eq_parts.append(f"{open_stock_funds} stock fund(s)")
    fund_parts = [f"{nw_fund_count} mutual fund(s)"] if nw_fund_count else []
    if nw.get("annual_fund_income", 0) > 0:
        fund_parts.append(f"~{nw['annual_fund_income'] / 12.0:,.0f} {CCY}/mo expected")
    meta_by_key = {
        "stocks": " + ".join(eq_parts) + " · auto-synced",
        "us_stocks": f"{us_shares_total:g} share(s) · ${nw['usd_stock_usd']:,.2f} USD"
        if us_stock_items
        else "Add PCOR / ESPP shares below",
        "funds": " · ".join(fund_parts) if fund_parts else "Add money-market / fixed-income funds below",
        "gold": f"{nw['gold_total_grams']:,.1f}g total ({nw['gold_grams_24k']:g}g 24K · {nw['gold_grams_21k']:g}g 21K)"
        if nw["gold_total_grams"] > 0
        else "No gold items added",
        "fx": f"${nw['usd_cash_usd']:,.0f} USD cash" if nw["usd_cash_usd"] > 0 else (f"{fx_count} currency balance(s)" if fx_count else "No FX cash added"),
        "cd_cash": " · ".join(cd_parts) if cd_parts else "EGP & FX certificates / cash",
    }

    st.markdown(_nw_bars_html(nw, meta_by_key), unsafe_allow_html=True)

    # --- Net Worth Over Time (Snapshots Chart) ----------------------------------------
    series = build_snapshot_series(nw_snapshots, today_iso, nw)
    if series:
        components.html(nw_chart_html(series, CCY), height=NW_CHART_HEIGHT)
        st.caption(
            "Snapshots are saved automatically whenever you sync/save stock prices or update Net Worth items & rates "
            "(one point per day — same-day edits overwrite that day's snapshot). "
            "Use **📸 Snapshot / Backfill** above to record or backfill any historical date."
        )
        if nw_snapshots:
            with st.expander(f"🗂 Saved Snapshots ({len(nw_snapshots)}) — view or delete"):
                snap_rows = [
                    {
                        "Date": r["date"],
                        f"Total ({CCY})": float(r["total_egp"]),
                        f"EGX Equities ({CCY})": float(r["stocks_egp"]),
                        f"US Stock ({CCY})": float(r["us_stocks_egp"]),
                        f"Mutual Funds ({CCY})": float(r["funds_egp"]),
                        f"Gold ({CCY})": float(r["gold_egp"]),
                        f"FX Cash ({CCY})": float(r["fx_egp"]),
                        f"CDs & Cash ({CCY})": float(r["cd_cash_egp"]),
                        f"USD Exposure ({CCY})": float(r.get("usd_total_egp", 0.0)),
                    }
                    for r in reversed(nw_snapshots)
                ]
                snap_df = pd.DataFrame(snap_rows)
                snap_evt = st.dataframe(
                    snap_df,
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row",
                    key="nw_snap_table",
                    column_config={
                        col: st.column_config.NumberColumn(format="%.0f")
                        for col in snap_df.columns
                        if col != "Date"
                    },
                )
                sel_snap = snap_evt.selection.rows
                if sel_snap:
                    chosen_snap = snap_rows[sel_snap[0]]
                    del_s_col, _ = st.columns([1.2, 4])
                    if del_s_col.button(
                        f"🗑 Delete snapshot {chosen_snap['Date']}",
                        key="nw_del_snap_btn",
                        use_container_width=True,
                    ):
                        store.delete_net_worth_snapshot(str(chosen_snap["Date"]))
                        st.session_state["toast"] = f"Deleted snapshot for {chosen_snap['Date']}."
                        st.rerun()
                else:
                    st.caption("Select a snapshot row above to delete it, or click **📸 Snapshot / Backfill** to edit any date.")

    # --- Non-Stock Holdings Table (US Stock, Mutual Funds, Gold, FX, CDs, Cash) -------
    st.subheader("Manual Holdings (US Stock, Mutual Funds, Gold, FX, CDs & Cash)")
    if not items:
        st.info(
            "No manual items added yet. Click **➕ Add Net Worth Item** above "
            "to add your PCOR/ESPP shares, mutual funds, certificates (EGP or USD/FX), currency balances, or gold."
        )
        return

    gold_unpriced = any(it.category == CAT_GOLD for it in items) and p24 == 0 and p21 == 0
    if gold_unpriced:
        st.warning("Enter the **24K** or **21K** gold gram price above so your gold items can be valued in EGP.")

    table_rows = []
    for it in items:
        val_egp = item_value_egp(it, p24, p21, usd_rate)
        ccy = item_currency(it)
        eff_rate = effective_fx_rate(it, usd_rate)
        if it.category == CAT_GOLD:
            active_px = p21 if it.karat == 21 else p24
            qty_str = f"{it.quantity:g} pcs ({it.quantity * it.grams:g}g)"
            rate_str = f"{it.karat}K @ {active_px:,.0f}/g" + (f" (+{it.unit_value:g}/g)" if it.unit_value > 0 else "")
        elif it.category == CAT_US_STOCK:
            native_v = item_native_value(it)
            qty_str = f"{it.quantity:g} sh (${native_v:,.2f} {ccy})"
            rate_str = f"${it.unit_value:,.2f}/sh · 1 {ccy} = {eff_rate:,.2f} {CCY}"
        elif it.category == CAT_FUND:
            native_v = item_native_value(it)
            yr_native = item_annual_income_native(it)
            yr_egp = item_annual_income_egp(it, usd_rate)
            if it.unit_value > 0:
                qty_str = f"{it.quantity:,.4g} units ({native_v:,.2f} {ccy})"
                details = [f"NAV = {it.unit_value:,.4f} {ccy}"]
            else:
                qty_str = f"{native_v:,.2f} {ccy}"
                details = ["Direct balance"]
            if ccy != "EGP":
                details.append(f"1 {ccy} = {eff_rate:,.2f} {CCY}")
            if yr_egp > 0:
                if ccy != "EGP":
                    details.append(f"~{it.grams:g}%/yr (~{yr_native / 12.0:,.1f} {ccy} · ~{yr_egp / 12.0:,.0f} {CCY}/mo)")
                else:
                    details.append(f"~{it.grams:g}%/yr (~{yr_egp / 12.0:,.0f} {CCY}/mo)")
            rate_str = " · ".join(details)
        elif it.category == CAT_FX:
            qty_str = f"{it.quantity:,.2f} {ccy}"
            rate_str = f"1 {ccy} = {eff_rate:,.2f} {CCY}"
        else:
            qty_str = f"{it.quantity:,.2f} {ccy}"
            yr_native = item_annual_income_native(it)
            yr_egp = item_annual_income_egp(it, usd_rate)
            details = []
            if ccy != "EGP":
                details.append(f"1 {ccy} = {eff_rate:,.2f} {CCY}")
            if yr_egp > 0:
                if ccy != "EGP":
                    details.append(f"{it.unit_value:g}%/yr ({yr_native / 12.0:,.1f} {ccy} · {yr_egp / 12.0:,.0f} {CCY}/mo)")
                else:
                    details.append(f"{it.unit_value:g}%/yr ({yr_egp / 12.0:,.0f} {CCY}/mo)")
            rate_str = " · ".join(details) if details else "—"

        table_rows.append(
            {
                "Type": _NW_CAT_SHORT.get(it.category, it.category) + (f" ({ccy})" if it.category in (CAT_CD, CAT_CASH, CAT_FUND) and ccy != "EGP" else ""),
                "Item": it.name,
                "Principal / Qty": qty_str,
                "Rate / Details": rate_str,
                f"Value ({CCY})": val_egp,
                "Net Worth %": (val_egp / nw["total"] * 100.0) if nw["total"] > 0 else 0.0,
                "Notes": it.notes or "—",
            }
        )

    nw_df = pd.DataFrame(table_rows)
    event = st.dataframe(
        nw_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="nw_table",
        column_config={
            f"Value ({CCY})": st.column_config.NumberColumn(format="%.2f"),
            "Net Worth %": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

    sel = event.selection.rows
    if not sel:
        st.caption("Select a row to edit its balance/rate or delete it.")
        return
    chosen_row = nw_rows[sel[0]]
    st.caption(f"Selected: **{_NW_CAT_SHORT.get(chosen_row['category'], '')} · {chosen_row['name']}**")
    edit_col, del_col, _ = st.columns([1, 1, 4])
    if edit_col.button("✏️ Edit", key="nw_edit", use_container_width=True):
        net_worth_dialog(chosen_row, _new_dialog_nonce(), p24, p21, usd_rate)
    with del_col.popover("🗑 Delete", use_container_width=True):
        st.write(f"Delete **{chosen_row['name']}**?")
        if st.button("Confirm delete", type="primary", key="nw_confirm_delete"):
            store.delete_net_worth_item(int(chosen_row["id"]))
            record_current_snapshot()
            st.session_state["toast"] = f"Deleted {chosen_row['name']}."
            st.rerun()


with tab_networth:
    net_worth_tab(
        positions, fund_symbols, all_nw_rows, all_nw_snapshots,
        adj_txns, price_history, saved_price_entries,
    )

