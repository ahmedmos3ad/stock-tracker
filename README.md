# Portfolio & Stock Tracker

A local dashboard to track your **EGX / THNDR stock positions** alongside your full **Net Worth** in **EGP** (US / Employer Stock, Mutual Funds, Gold bars & pounds, Foreign Currency, and EGP/USD Certificates of Deposit).

Its core stock engine solves a specific problem: brokers like THNDR use a *moving weighted-average* cost. When you sell high and re-buy lower to "fix" your average (DCA), the broker's displayed average drifts down and no longer reflects reality. This app stores every transaction in a single SQLite file (`portfolio.db`) and shows several views of each position side by side so you always know your *true* cost.

## Tabs Overview

| Tab | What it does |
| --- | --- |
| **📊 Portfolio** | Open & closed EGX stock and stock-fund positions with **Break-even**, **Broker avg (THNDR)**, **Pure avg buy**, **Realized P&L**, and **Unrealized P&L**. |
| **🥧 Allocation** | Wallet weights across open positions, weight at cost vs. market value, and oversized position flags. |
| **📈 Performance** | Money-weighted **XIRR** vs. **EGX30** and a **Deposit / T-bill rate**, interactive ThndrX-style Return/Equity curve (`1D`, `1M`, `6M`, `1Y`, `YTD`, `All`), and per-stock returns. |
| **🧮 Buy / Sell Planner** | Simulate DCA buys (new break-even, new broker avg, wallet share, efficiency per 1,000 EGP) or trimming/selling positions. |
| **💲 Prices** | One-click **EODHD** close sync for open EGX stocks, manual NAV entry for stock funds, and manual EGX30 benchmark level. |
| **🧾 Transactions** | Full buy/sell ledger + **Corporate Actions** (bonus shares and forward splits). |
| **💰 Dividends** | Cash dividends received per stock (factored into total return and performance charts). |
| **🏦 Net Worth** | Full Net Worth breakdown in **EGP** + **USD Exposure %** across auto-synced **EGX Equities**, **US / Employer Stock (`PCOR` / ESPP)**, **Mutual Funds** (with expected rate), **Gold** (24K/21K bars & pounds), **Foreign Currency**, and **CDs & Cash** (EGP or FX). |

## What it shows per stock position

| Metric | Meaning |
| --- | --- |
| **Broker avg (THNDR)** | Weighted-average cost. Buys move it; sells don't. Matches what THNDR shows. |
| **Your break-even** | `(money in − money out) ÷ shares held`. The price to sell everything and net zero — includes realized gains from round-trip trades. |
| **Pure avg buy** | Average of your buy prices only, ignoring sells and fees. |
| **Realized P&L** | Profit/loss already locked in by your sells (plus dividends). |
| **Unrealized P&L** | Paper profit/loss on shares you still hold (needs a current price). |

## Requirements

- Python 3.9 or newer
- The packages in `requirements.txt`

## Setup & Run (Windows)

Open **PowerShell** or **Command Prompt** in this folder:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Streamlit opens the dashboard in your browser (usually http://localhost:8501).
Your data is saved in `portfolio.db` next to the app — back up that single file to back up everything.

## Setup & Run (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## EODHD price sync

To use the one-click EOD close sync and historical backfill, set `EODHD_API_KEY` in a `.env` file or environment variable before launching the app, or paste the key in the **💲 Prices** tab.
The free EODHD plan is limited to 20 API calls per day and one year of historical EOD data; only open stock positions are synced (funds are priced manually and never sent to EODHD).

## Tests

Run the unit test suite (requires no external dependencies):

```bash
python3 -m unittest discover -s tests -v
```

## Project layout

```
app.py                  # Streamlit dashboard (UI)
tracker/portfolio.py    # Cost-basis & P&L engine (pure, testable, no DB/UI)
tracker/performance.py  # XIRR, daily equity curve, and EGX30/deposit benchmarks
tracker/chart.py        # Interactive HTML/SVG Return & Equity analytics card
tracker/calculator.py   # DCA buy and sell/trim simulators + brokerage fee math
tracker/actions.py      # Corporate actions (bonus shares & forward splits)
tracker/assets.py       # Stock vs. manual-NAV fund rules and staleness checks
tracker/networth.py     # Net Worth & USD/currency exposure across all asset classes
tracker/prices.py       # EODHD latest close fetcher
tracker/history.py      # EODHD historical daily closes fetcher
tracker/companies.py    # EGX company names, domains, and logo resolver
tracker/db.py           # SQLite store for transactions, prices, settings & net worth
portfolio.db            # Your data (single SQLite file)
```
