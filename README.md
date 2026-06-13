# Stock Tracker

A tiny local app to track EGX / THNDR stock positions in **EGP**, focused on one
problem: brokers like THNDR use a *moving weighted-average* cost. When you sell
high and re-buy lower to "fix" your average (DCA), the broker's displayed average
drifts down and no longer reflects reality. This app stores every transaction in
a small SQLite file and shows several views of each position side by side so you
always know your *true* cost.

## What it shows per position

| Metric | Meaning |
| --- | --- |
| **Broker avg (THNDR)** | Weighted-average cost. Buys move it; sells don't. Matches what THNDR shows. |
| **Your break-even** | `(money in − money out) ÷ shares held`. The price to sell everything and net zero — includes realized gains from round-trip trades. |
| **Pure avg buy** | Average of your buy prices only, ignoring sells and fees. |
| **Realized P&L** | Profit/loss already locked in by your sells. |
| **Unrealized P&L** | Paper profit/loss on shares you still hold (needs a current price). |

## Requirements

- Python 3.10 or newer
- The packages in `requirements.txt`

## Setup (Windows)

Open **PowerShell** or **Command Prompt** in this folder:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bat
streamlit run app.py
```

Streamlit opens the dashboard in your browser (usually http://localhost:8501).
Your data is saved in `portfolio.db` next to the app — back up that single file
to back up everything.

## EODHD price sync

To use the one-click EOD close sync in the Prices tab, set `EODHD_API_KEY`
before launching the app, or paste the key into the sidebar after it opens.
The exchange code defaults to `CA` for EGX symbols, and you can override it
with `EODHD_EXCHANGE_CODE` if needed.

The free EODHD plan is enough for manual syncs, but it is limited to 20 API
calls per day and one year of historical EOD data.

## Setup / Run (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Project layout

```
app.py                # Streamlit dashboard (UI only)
tracker/portfolio.py  # Cost-basis math (pure, testable, no DB/UI)
tracker/db.py         # SQLite transaction + price store
portfolio.db          # Your data (created on first run)
```
