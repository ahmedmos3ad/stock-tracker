"""Read-only check: does EODHD agree with the prices we trust from the ledger?

Uses the EODHD key from .env / environment (never printed). Does not write to the database.
Costs one API call per symbol (+1 for EGX30).

Trust anchors:
  1. every trade price must lie inside that day's EODHD [Low, High] (a real fill cannot be outside it)
  2. the 6 prices saved on 2026-06-11 (legacy price_history) vs EODHD's close that day
"""
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

from dotenv import load_dotenv

root = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=root / ".env")
key = os.getenv("EODHD_API_KEY", "").strip()
if not key:
    sys.exit("No EODHD_API_KEY found in .env / environment.")

c = sqlite3.connect(f"file:{(root / 'portfolio.db').as_posix()}?mode=ro", uri=True)
trades = c.execute("SELECT symbol, date, price, side FROM transactions ORDER BY symbol, date").fetchall()
saved = c.execute("SELECT symbol, substr(updated_at,1,10), current_price FROM price_history ORDER BY id").fetchall()
syms = sorted({t[0] for t in trades})
start = min(t[1] for t in trades)


def fetch(code):
    url = f"https://eodhd.com/api/eod/{quote(code)}?api_token={quote(key)}&fmt=json&period=d&from={start}"
    try:
        with urlopen(url, timeout=30) as r:
            data = json.loads(r.read().decode())
    except HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, type(e).__name__
    return {b["date"][:10]: (float(b["low"]), float(b["high"]), float(b["close"]), float(b.get("adjusted_close") or 0)) for b in data}, None


bars = {}
for s in syms:
    bars[s], err = fetch(s + ".EGX")
    if bars[s] is None:
        print("%-5s FAILED: %s" % (s, err))
        bars[s] = {}
    else:
        print("%-5s bars=%d first=%s last=%s" % (s, len(bars[s]), min(bars[s], default="-"), max(bars[s], default="-")))

idx, err = fetch("EGX30.INDX")
print("EGX30.INDX:", ("FAILED: " + err) if idx is None else "bars=%d first=%s last=%s" % (len(idx), min(idx), max(idx)))

print("\n== 1. trade price inside EODHD's day range (0.5%% tolerance) ==")
inside = outside = missing = 0
bad = []
for s, d, p, side in trades:
    b = bars.get(s, {}).get(d)
    if not b:
        missing += 1
        continue
    if b[0] * 0.995 <= p <= b[1] * 1.005:
        inside += 1
    else:
        outside += 1
        bad.append((s, d, side, p, round(b[0], 2), round(b[1], 2)))
print("inside=%d outside=%d no_bar_that_day=%d (of %d trades)" % (inside, outside, missing, len(trades)))
for row in bad[:12]:
    print("  OUTSIDE:", row, "(symbol, date, side, my price, eodhd low, eodhd high)")

print("\n== 2. saved 2026-06-11 prices vs EODHD close ==")
for s, d, p in saved:
    b = bars.get(s, {}).get(d)
    print("%-5s %s saved=%-8s eodhd_close=%s" % (s, d, p, "n/a" if not b else round(b[2], 2)))

# Raw close vs adjusted close: they should be identical unless a split/bonus/dividend adjustment applies.
diff = [(s, d) for s in bars for d, b in bars[s].items() if b[3] and abs(b[2] - b[3]) / b[2] > 0.001]
print("\n== 3. bars where adjusted_close differs from close by >0.1%%: %d ==" % len(diff))
for s in sorted({x[0] for x in diff}):
    print("  %s: %d bars differ (e.g. %s)" % (s, sum(1 for x in diff if x[0] == s), min(d for ss, d in diff if ss == s)))
