"""Headless smoke test of the Net Worth tab (run with the Python that has streamlit installed).

Runs against a THROWAWAY copy of portfolio.db, seeded with a debt and a CD maturing soon so every
Net Worth section renders, then exercises the planning inputs (targets, expenses, inflation, limit).
"""
import shutil
import sqlite3
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.stdout.reconfigure(encoding="utf-8")

tmp = Path(tempfile.mkdtemp()) / "copy.db"
shutil.copyfile(root / "portfolio.db", tmp)
import tracker.db as dbmod  # noqa: E402

dbmod.Store.__init__.__defaults__ = (tmp,)
seed = dbmod.Store(tmp)  # migrates the copy
from tracker.prices import CAIRO_TZ  # noqa: E402
from datetime import datetime  # noqa: E402

today = datetime.now(CAIRO_TZ).date()
seed.add_net_worth_item("liability", "Smoke loan", 50000.0, 20.0, monthly_amount=2500.0,
                        maturity_date=(today + timedelta(days=400)).isoformat())
seed.add_net_worth_item("liability", "Smoke USD card", 100.0, 0.0, currency="USD", fx_rate=50.0)
seed.add_net_worth_item("cd", "Smoke CD soon", 10000.0, 20.0, maturity_date=(today + timedelta(days=10)).isoformat())
seed.add_net_worth_item("cd", "Smoke CD past", 5000.0, 20.0, maturity_date=(today - timedelta(days=3)).isoformat())
seed.close()

from streamlit.testing.v1 import AppTest  # noqa: E402


def check(label, at):
    errs = [str(e.value) for e in at.exception]
    print("%-40s exceptions=%s" % (label, errs or "none"))
    assert not errs, errs


def saved():
    return dict(sqlite3.connect(tmp).execute("select key, value from settings").fetchall())


at = AppTest.from_file(str(root / "app.py"), default_timeout=180).run()
check("initial render", at)
text = " ".join(str(m.value) for m in at.markdown) + " ".join(str(s.value) for s in at.subheader)
for needle in ("What Changed", "Planning & Risk", "Debts (owed)", "Manual Holdings"):
    assert needle in text, f"missing section: {needle}"
metric_labels = [m.label for m in at.metric]
assert "Net Worth" in metric_labels and "Debts" in metric_labels, metric_labels
alerts = " ".join(str(x.value) for x in list(at.warning) + list(at.info) + list(at.error))
assert "Smoke CD soon" in alerts and "Smoke CD past" in alerts, "maturity alerts missing"
print("sections, metrics and maturity alerts present")

at.number_input(key="nw_tgt_gold").set_value(10.0).run()
check("set gold target", at)
at.number_input(key="nw_tgt_usd").set_value(60.0).run()
check("set USD target", at)
at.number_input(key="nw_expenses").set_value(40000.0).run()
check("set monthly expenses", at)
at.number_input(key="nw_conc_limit").set_value(10.0).run()
check("set concentration limit", at)
if at.number_input(key="nw_inflation") is not None:
    at.number_input(key="nw_inflation").set_value(12.0).run()
    check("set inflation", at)
s = saved()
for k, v in {"nw_target_gold": "10.0", "nw_target_usd": "60.0", "nw_monthly_expenses": "40000.0",
             "nw_concentration_limit": "10.0"}.items():
    assert s.get(k) == v, (k, s.get(k))
print("planning settings persisted:", {k: s[k] for k in s if k.startswith("nw_")})

at.number_input(key="nw_usd_rate").set_value(51.25).run()
check("edit USD rate", at)
s = saved()
assert s["usd_egp_rate"] == "51.25" and s.get("usd_egp_rate_updated_at"), s
snap = sqlite3.connect(tmp).execute(
    "select usd_egp_rate, liabilities_egp from net_worth_snapshots order by date desc limit 1"
).fetchone()
assert abs(snap[0] - 51.25) < 1e-9 and snap[1] > 0, snap
print("rate edit stamped + snapshot records rate/debts:", snap)

b = AppTest.from_file(str(root / "app.py"), default_timeout=180).run()
check("fresh session", b)
assert b.number_input(key="nw_tgt_gold").value == 10.0
assert b.number_input(key="nw_expenses").value == 40000.0
print("\nNET WORTH SMOKE OK")
