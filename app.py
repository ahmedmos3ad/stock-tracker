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

import pandas as pd
import streamlit as st

from tracker import BUY, SELL, Store, Transaction, compute_positions, companies
from tracker import performance as perf
from tracker.actions import apply_corporate_actions, resolve_actions
from tracker.assets import fund_list, stale_navs, stock_symbols
from tracker.calculator import (
    DEFAULT_FEE_RATE,
    estimate_fee,
    shares_to_trim,
    simulate_dca,
    simulate_sell,
)
from tracker.history import EGX30_SYMBOL, fetch_history
from tracker.portfolio import Dividend
from tracker.prices import fetch_latest_eodhd_close

CCY = "EGP"
OTHER = "__OTHER__"


def cairo_today() -> date:
    return datetime.now(ZoneInfo("Africa/Cairo")).date()


def cairo_now() -> datetime:
    return datetime.now(ZoneInfo("Africa/Cairo"))

st.set_page_config(page_title="Stock Tracker", page_icon=":chart_with_upwards_trend:", layout="wide")


@st.cache_resource
def get_store() -> Store:
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
        "set_setting",
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
    auto_fee = estimate_fee(quantity or 0, unit_price or 0)
    if quantity and unit_price:
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
                fee=fee if fee else auto_fee, date=txn_date.isoformat(),
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
    all_action_rows, price_history, fund_rows, settings_rows,
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


def _save_setting(widget_key: str, setting_key: str) -> None:
    """on_change callback: write the widget's new value to the database."""
    store.set_setting(setting_key, st.session_state[widget_key])
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
    st.title("📈 Stock Tracker")
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

(tab_portfolio, tab_allocation, tab_perf, tab_dca, tab_prices, tab_txns, tab_dividends) = st.tabs(
    [
        "📊 Portfolio", "🥧 Allocation", "📈 Performance", "🧮 Buy / Sell Planner",
        "💲 Prices", "🧾 Transactions", "💰 Dividends",
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
            st.session_state["toast"] = f"Saved {changed} price(s)."
            st.rerun()
        else:
            st.toast("No price changes to save.")


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
def sell_planner(held: dict, saved_prices: dict) -> None:
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
            help=f"Total brokerage fee for this sale. Empty = {DEFAULT_FEE_RATE * 100:.3f}% of the trade value.",
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
            fee if fee else estimate_fee(sell_qty, sell_price),
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
            fee if fee else estimate_fee(q, sell_price),
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
def dca_tab(positions, saved_prices: dict) -> None:
    held = {p.symbol: p for p in positions if p.quantity > 0}
    if not held:
        st.info("No open positions to plan with.")
        return

    action = st.radio(
        "Plan", ["Buy more (DCA)", "Sell / trim"], horizontal=True, key="plan_action",
        label_visibility="collapsed",
    )
    if action == "Sell / trim":
        sell_planner(held, saved_prices)
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
            help=f"Total brokerage fee for this buy. Empty = {DEFAULT_FEE_RATE * 100:.3f}% of the trade value.",
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
            fee if fee else estimate_fee(add_qty, buy_price),
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
            fee if fee else estimate_fee(q, buy_price),
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
    dca_tab(positions, saved_prices)


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
    st.subheader("Portfolio value vs money invested")
    curve = perf.equity_curve(adj_txns, curve_history, end=today)
    df = pd.DataFrame(curve)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    span = st.radio("Range", ["1M", "3M", "6M", "1Y", "All"], index=4, horizontal=True,
                    key="perf_range", label_visibility="collapsed")
    window = {"1M": 30, "3M": 91, "6M": 182, "1Y": 365}.get(span)
    if window:
        df = df[df.index >= df.index.max() - pd.Timedelta(days=window)]
    st.line_chart(
        df.rename(columns={"value": "Portfolio value", "contributed": "Net cash invested"})[
            ["Portfolio value", "Net cash invested"]
        ],
        color=["#2ecc71", "#9aa0a6"],
    )
    priced_days = len({d for s in held_symbols for d, _ in history.get(s, [])})
    st.caption(
        f"Gap between the lines = trading profit (dividends excluded). Built from {priced_days:,} days of saved "
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
