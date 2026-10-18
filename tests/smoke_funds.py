"""End-to-end check of the stock/fund split against a THROWAWAY copy of the DB (real DB untouched).

Verifies: funds are never requested from EODHD, closed positions aren't synced, fund inputs are labelled NAV,
stale-NAV warning appears, FUND tag shows in the allocation, and flagging a symbol via the UI persists.
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.stdout.reconfigure(encoding="utf-8")

tmp = Path(tempfile.mkdtemp()) / "copy.db"
shutil.copyfile(root / "portfolio.db", tmp)

import tracker.db as dbmod  # noqa: E402

dbmod.Store.__init__.__defaults__ = (tmp,)
store = dbmod.Store(tmp)
store.add_transaction(dbmod.Transaction("BAL", "buy", 1000, 1.40, 0.4, "2026-09-01"))  # an OPEN fund position
store.set_fund("BAL", True)

import tracker.prices as prices_mod  # noqa: E402

requested = []


def fake_fetch(symbol, key):
    requested.append(symbol)
    return None  # pretend EODHD has nothing; we only care WHICH symbols were asked for


prices_mod.fetch_latest_eodhd_close = fake_fetch

import tracker.history as history_mod  # noqa: E402

hist_requested = []


def fake_history(symbol, start, key):
    hist_requested.append(symbol)
    return None, "stub"  # nothing is written; we only record WHICH symbols were requested


history_mod.fetch_history = fake_history

from streamlit.testing.v1 import AppTest  # noqa: E402

at = AppTest.from_file(str(root / "app.py"), default_timeout=120).run()
assert not at.exception, [str(e.value) for e in at.exception]
print("render ok with an open fund position")

keys = {n.key for n in at.number_input if n.key}
assert "price_BAL" in keys and "price_ABUK" in keys, "open fund + open stock should both get a price box"
assert "price_ARCC" not in keys and "price_BWA" not in keys, "closed positions must not get a price box"
bal = next(n for n in at.number_input if n.key == "price_BAL")
assert "NAV" in bal.label, bal.label
print("price boxes: open stocks + open fund only; fund labelled:", bal.label)

assert any("Update fund NAV" in w.value and "BAL" in w.value for w in at.warning), [w.value for w in at.warning]
print("stale-NAV warning shown for BAL")

assert any("FUND" in m.value for m in at.markdown), "allocation should tag funds"
print("FUND tag present in the allocation view")

sync = next(b for b in at.button if b.label.startswith("Sync"))
print("sync button:", sync.label)
at.text_input(key="tab_eodhd_api_key").set_value("dummy-key").run()
sync = next(b for b in at.button if b.label.startswith("Sync"))
sync.click().run()
assert not at.exception, [str(e.value) for e in at.exception]
print("symbols requested from EODHD:", sorted(requested))
assert "BAL" not in requested, "a fund was sent to EODHD!"
assert "ARCC" not in requested, "a closed position was sent to EODHD"
assert "ABUK" in requested
msg = st_msg = [i.value for i in at.info if "manually" in i.value]
assert msg and "BAL" in msg[0], msg
print("sync message mentions the skipped fund:", msg[0][-60:])

# the history BACKFILL must never request a fund either
backfill = next(b for b in at.button if "Backfill" in b.label)
backfill.click().run()
assert not at.exception, [str(e.value) for e in at.exception]
print("symbols requested for history:", sorted(hist_requested))
assert hist_requested, "backfill did not run"
funds_now = {"BAL", "BMM", "BWA", "CI30"}
assert not funds_now & set(hist_requested), "a fund was sent to the EODHD history endpoint!"
assert "ARCC" in hist_requested or "ABUK" in hist_requested
assert "EGX30.INDX" in hist_requested

# flag another symbol as a fund through the UI picker and confirm it is persisted
picker = next(m for m in at.multiselect if m.key and m.key.startswith("fund_picker_"))
picker.set_value(["BAL", "FWRY"]).run()
saved = sqlite3.connect(tmp).execute("select symbol from assets where is_fund=1 order by 1").fetchall()
print("funds in DB after using the picker:", [r[0] for r in saved])
assert [r[0] for r in saved] == ["BAL", "FWRY"]
assert not at.exception
print("\nFUND SPLIT OK")
