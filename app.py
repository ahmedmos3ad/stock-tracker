"""Streamlit dashboard for tracking EGX/THNDR stock positions.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from tracker import BUY, SELL, Store, Transaction, compute_positions, companies

CCY = "EGP"
OTHER = "__OTHER__"


def cairo_today() -> date:
    return datetime.now(ZoneInfo("Africa/Cairo")).date()


def cairo_now() -> datetime:
    return datetime.now(ZoneInfo("Africa/Cairo"))

st.set_page_config(page_title="Stock Tracker", page_icon=":chart_with_upwards_trend:", layout="wide")


@st.cache_resource
def get_store() -> Store:
    return Store()


store = get_store()

if "show_add_txn" not in st.session_state:
    st.session_state["show_add_txn"] = False
if "add_txn_stage" not in st.session_state:
    st.session_state["add_txn_stage"] = "form"
if "add_txn_message" not in st.session_state:
    st.session_state["add_txn_message"] = None
if "txn_dialog_mode" not in st.session_state:
    st.session_state["txn_dialog_mode"] = "add"
if "txn_dialog_id" not in st.session_state:
    st.session_state["txn_dialog_id"] = None


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


def reset_txn_form_state() -> None:
    st.session_state["sel_symbol"] = OTHER
    st.session_state["custom_symbol"] = ""
    st.session_state["txn_side"] = BUY
    st.session_state["txn_date"] = date.today()
    st.session_state["txn_quantity"] = None
    st.session_state["txn_price"] = None
    st.session_state["txn_fee"] = None


def load_txn_form_state(txn: dict) -> None:
    st.session_state["sel_symbol"] = txn["symbol"] if txn["symbol"] in companies.COMPANIES else OTHER
    st.session_state["custom_symbol"] = "" if txn["symbol"] in companies.COMPANIES else txn["symbol"]
    st.session_state["txn_side"] = txn["side"]
    st.session_state["txn_date"] = date.fromisoformat(txn["date"])
    st.session_state["txn_quantity"] = int(txn["quantity"])
    st.session_state["txn_price"] = float(txn["price"])
    st.session_state["txn_fee"] = float(txn["fee"])


def open_add_transaction_dialog() -> None:
    st.session_state["show_add_txn"] = True
    st.session_state["txn_dialog_mode"] = "add"
    st.session_state["txn_dialog_id"] = None
    st.session_state["add_txn_stage"] = "form"
    st.session_state["add_txn_message"] = None
    reset_txn_form_state()


def open_edit_transaction_dialog(txn: dict) -> None:
    st.session_state["show_add_txn"] = True
    st.session_state["txn_dialog_mode"] = "edit"
    st.session_state["txn_dialog_id"] = txn["id"]
    st.session_state["add_txn_stage"] = "form"
    st.session_state["add_txn_message"] = None
    load_txn_form_state(txn)


def close_transaction_dialog() -> None:
    st.session_state["show_add_txn"] = False
    st.session_state["txn_dialog_mode"] = "add"
    st.session_state["txn_dialog_id"] = None
    st.session_state["add_txn_stage"] = "form"
    st.session_state["add_txn_message"] = None
    st.session_state.pop("sel_symbol", None)
    st.session_state.pop("custom_symbol", None)
    st.session_state.pop("txn_side", None)
    st.session_state.pop("txn_date", None)
    st.session_state.pop("txn_quantity", None)
    st.session_state.pop("txn_price", None)
    st.session_state.pop("txn_fee", None)


def success_panel(message: str) -> None:
    st.markdown(
        """
        <style>
        @keyframes addTxnPop {
            0% { opacity: 0; transform: translateY(-6px) scale(0.98); }
            100% { opacity: 1; transform: translateY(0) scale(1); }
        }
        .add-txn-success {
            background: rgba(46, 204, 113, 0.14);
            border: 1px solid rgba(46, 204, 113, 0.35);
            color: #1f7a4f;
            padding: 0.8rem 0.9rem;
            border-radius: 0.8rem;
            font-weight: 600;
            animation: addTxnPop 180ms ease-out;
            margin-bottom: 0.75rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div class='add-txn-success'>✅ {message}<br><span style='font-weight:500'>Create another or close the dialog.</span></div>",
        unsafe_allow_html=True,
    )


@st.dialog("🧾 Transaction", width="large")
def add_transaction_dialog() -> None:
    mode = st.session_state["txn_dialog_mode"]
    if st.session_state["add_txn_stage"] == "success":
        if mode == "edit":
            success_panel(st.session_state["add_txn_message"] or "Transaction updated.")
            if st.button("Close", use_container_width=True):
                close_transaction_dialog()
                st.rerun()
        else:
            success_panel(st.session_state["add_txn_message"] or "Transaction added.")
            c1, c2 = st.columns(2)
            if c1.button("Create another", type="primary", use_container_width=True):
                st.session_state["add_txn_stage"] = "form"
                st.session_state["add_txn_message"] = None
                st.session_state["txn_dialog_mode"] = "add"
                st.session_state["txn_dialog_id"] = None
                reset_txn_form_state()
                st.rerun()
            if c2.button("Close", use_container_width=True):
                close_transaction_dialog()
                st.rerun()
        return

    # Company selector is outside the form so the logo preview updates live.
    options = sorted(companies.COMPANIES.keys()) + [OTHER]
    logo_col, pick_col = st.columns([1, 6], vertical_alignment="center")
    with pick_col:
        selected = st.selectbox(
            "Company",
            options,
            format_func=lambda s: "Other (type a symbol)…" if s == OTHER else companies.display_label(s),
            key="sel_symbol",
        )
        if selected == OTHER:
            symbol = st.text_input("Symbol", placeholder="e.g. RACC", key="custom_symbol").strip().upper()
        else:
            symbol = selected
    with logo_col:
        render_logo(symbol)

    with st.form("add_txn", clear_on_submit=True, border=False):
        c1, c2 = st.columns(2)
        side = c1.selectbox("Side", [BUY, SELL], format_func=str.capitalize, key="txn_side")
        txn_date = c2.date_input("Date", key="txn_date")
        quantity = st.number_input(
            "Quantity",
            min_value=1,
            step=1,
            placeholder="0",
            key="txn_quantity",
        )
        c4, c5 = st.columns(2)
        unit_price = c4.number_input(
            f"Price ({CCY})",
            min_value=0.0,
            step=0.01,
            format="%.4f",
            placeholder="0.00",
            key="txn_price",
        )
        fee = c5.number_input(
            f"Fee ({CCY})",
            min_value=0.0,
            step=0.01,
            format="%.2f",
            placeholder="0.00",
            key="txn_fee",
        )
        st.write("")
        submit_label = "Save changes" if mode == "edit" else "Add transaction"
        submitted = st.form_submit_button(submit_label, type="primary", use_container_width=True)
        if submitted:
            if not symbol:
                st.error("Pick a company or type a symbol.")
            elif not quantity or not unit_price:
                st.error("Quantity and price must be greater than zero.")
            else:
                txn = Transaction(
                    symbol=symbol,
                    side=side,
                    quantity=int(quantity),
                    price=unit_price,
                    fee=fee or 0.0,
                    date=txn_date.isoformat(),
                )
                if mode == "edit":
                    txn_id = st.session_state.get("txn_dialog_id")
                    if txn_id is None:
                        st.error("No transaction selected for editing.")
                    else:
                        store.update_transaction(int(txn_id), txn)
                        st.session_state["add_txn_message"] = f"Updated {side} {int(quantity)} {symbol} @ {unit_price:g} {CCY}."
                        st.session_state["add_txn_stage"] = "success"
                        st.rerun()
                else:
                    store.add_transaction(txn)
                    st.session_state["add_txn_message"] = f"Added {side} {int(quantity)} {symbol} @ {unit_price:g} {CCY}."
                    st.session_state["add_txn_stage"] = "success"
                    st.rerun()

    if st.session_state["add_txn_stage"] == "form":
        if st.button("Close", use_container_width=True):
            close_transaction_dialog()
            st.rerun()


# --- Header ------------------------------------------------------------------
title_col, action_col = st.columns([5, 1], vertical_alignment="center")
with title_col:
    st.title("📈 Stock Tracker")
with action_col:
    if st.button("\\+ Add transaction", type="primary", use_container_width=True):
        open_add_transaction_dialog()

st.caption(
    "Tracks your *true* average across buy/sell round-trips — not the "
    "broker's moving average that drifts when you sell high and re-buy low."
)

if st.session_state["show_add_txn"]:
    add_transaction_dialog()

txns = store.transactions()
saved_prices = store.prices()
saved_price_entries = store.price_entries()

if not txns:
    st.info("No transactions yet. Click **+ Add transaction** to get started.")
    st.stop()

positions = compute_positions(txns, prices=saved_prices)
symbols = sorted({t.symbol for t in txns})

tab_portfolio, tab_prices, tab_txns = st.tabs(["📊 Portfolio", "💲 Prices", "🧾 Transactions"])

# --- Portfolio tab -----------------------------------------------------------
with tab_portfolio:
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

    rows = [
        {
            "Logo": logo_cell(p.symbol),
            "Symbol": p.symbol,
            "Your break-even": price(p.breakeven),
            "Break-even %": pct(p.breakeven_pct),
            "Current price": price(p.current_price),
            "Qty held": p.quantity,
            "Total P&L": money(p.total_pnl),
            "Total return %": pct(p.total_return_pct),
            "Unrealized P&L": money(p.unrealized_pnl),
            "Realized P&L": money(p.realized_pnl),
            "Market value": money(p.market_value),
            "Net invested": money(p.net_invested),
            "Broker avg (THNDR)": price(p.broker_avg) if p.quantity > 0 else "—",
            "Unrealized %": pct(p.unrealized_pct),
            "Pure avg buy": price(p.pure_avg_buy),
        }
        for p in positions
    ]
    colored_cols = [
        "Break-even %",
        "Total return %",
        "Realized P&L",
        "Unrealized P&L",
        "Total P&L",
    ]
    key_cols = ["Your break-even", "Break-even %"]
    dim_cols = ["Broker avg (THNDR)", "Unrealized %", "Pure avg buy"]
    styled = (
        pd.DataFrame(rows)
        .style.map(signed_css, subset=colored_cols)
        .set_properties(subset=colored_cols, **{"font-weight": "600"})
        .set_properties(
            subset=key_cols,
            **{"background-color": "rgba(76, 142, 219, 0.18)", "font-weight": "700"},
        )
        .set_properties(subset=dim_cols, **{"color": "#7a7f87"})
    )
    st.dataframe(
        styled,
        use_container_width=True,
        hide_index=True,
        column_config={"Logo": st.column_config.ImageColumn(" ", width="small")},
    )

    st.caption(
        "👉 **The highlighted columns are the ones to watch.** "
        "**Your break-even** = (money in − money out) ÷ shares held — your *true* average, "
        "including gains you already locked in. **Break-even %** = how far up/down you really "
        "are vs that break-even (if negative, you're genuinely down → a real DCA opportunity).  •  "
        "The dimmed columns are just for reference: **Broker avg (THNDR)** is the misleading "
        "weighted-average that drifts on re-buys, **Unrealized %** is up/down vs *that* number, "
        "and **Pure avg buy** ignores sells entirely."
    )

# --- Prices tab --------------------------------------------------------------
with tab_prices:
    st.caption("Enter the latest market price for each stock you hold. Used for unrealized P&L.")

    pcols = st.columns(min(4, len(symbols)) or 1)
    for i, sym in enumerate(symbols):
        col = pcols[i % len(pcols)]
        with col:
            head_logo, head_name = st.columns([1, 4], vertical_alignment="center")
            with head_logo:
                render_logo(sym, size=28)
            head_name.markdown(f"**{sym}**")
            saved = saved_prices.get(sym)
            new_price = st.number_input(
                f"Price ({CCY})",
                min_value=0.0,
                step=0.01,
                value=float(saved) if saved else None,
                format="%.4f",
                placeholder="0.00",
                key=f"price_{sym}",
                label_visibility="collapsed",
            )
            col.caption(freshness_label(saved_price_entries.get(sym, {}).get("updated_at")))
            if new_price and new_price != saved_prices.get(sym):
                store.set_price(sym, new_price, cairo_now().isoformat(timespec="seconds"))
                st.rerun()

# --- Transactions tab --------------------------------------------------------
with tab_txns:
    all_ledger = list(reversed(store.list_transactions()))
    pending_delete = st.session_state.get("pending_delete")

    symbol_options = ["All stocks"] + sorted({r["symbol"] for r in all_ledger})
    selected_symbol = st.selectbox(
        "Filter by stock symbol",
        symbol_options,
        key="txn_symbol_filter",
    )
    ledger = [r for r in all_ledger if selected_symbol == "All stocks" or r["symbol"] == selected_symbol]

    COL_WIDTHS = [2, 2, 1.2, 1.3, 1.6, 1.4, 1.8, 0.8]
    HEADERS = ["Date", "Symbol", "Side", "Qty", "Price", "Fee", "Total", ""]

    header_cols = st.columns(COL_WIDTHS)
    for col, label in zip(header_cols, HEADERS):
        col.markdown(f"<span style='color:#9aa0a6;font-size:12px;'>{label}</span>", unsafe_allow_html=True)
    st.divider()

    for r in ledger:
        cols = st.columns(COL_WIDTHS, vertical_alignment="center")
        is_buy = r["side"] == BUY
        total = r["quantity"] * r["price"] + (r["fee"] if is_buy else -r["fee"])
        cols[0].write(r["date"])
        cols[1].write(f"**{r['symbol']}**")
        cols[2].markdown(
            badge("BUY", "#2ecc71") if is_buy else badge("SELL", "#e74c3c"),
            unsafe_allow_html=True,
        )
        cols[3].write(f"{r['quantity']:g}")
        cols[4].write(f"{r['price']:g}")
        cols[5].write(f"{r['fee']:g}" if r["fee"] else "—")
        cols[6].write(f"{total:,.2f} {CCY}")
        if pending_delete == r["id"]:
            confirm, cancel = cols[7].columns(2)
            if confirm.button("✅", key=f"confirm_{r['id']}", help="Confirm delete"):
                close_transaction_dialog()
                store.delete_transaction(r["id"])
                st.session_state.pop("pending_delete", None)
                st.rerun()
            if cancel.button("✖️", key=f"cancel_{r['id']}", help="Cancel"):
                close_transaction_dialog()
                st.session_state.pop("pending_delete", None)
                st.rerun()
        else:
            edit_col, del_col = cols[7].columns([1, 1], gap="small")
            if edit_col.button("✏️", key=f"edit_{r['id']}", help="Edit this transaction"):
                open_edit_transaction_dialog(r)
                st.rerun()
            if del_col.button("🗑", key=f"del_{r['id']}", help="Delete this transaction"):
                close_transaction_dialog()
                st.session_state["pending_delete"] = r["id"]
                st.rerun()

    if pending_delete is not None and any(r["id"] == pending_delete for r in ledger):
        st.warning("Click ✅ to confirm deletion, or ✖️ to cancel.")
