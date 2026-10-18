"""Headless smoke test of the Streamlit app (run with the Python that has streamlit installed)."""
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

# Run against a THROWAWAY copy: the app persists settings (rate, size limit), so interacting with it
# against the real portfolio.db would overwrite the user's saved preferences.
import shutil
import tempfile

tmp = Path(tempfile.mkdtemp()) / "copy.db"
shutil.copyfile(root / "portfolio.db", tmp)
import tracker.db as dbmod

dbmod.Store.__init__.__defaults__ = (tmp,)

from streamlit.testing.v1 import AppTest


def ascii_(s):
    return str(s).encode("ascii", "replace").decode()


def report(label, at):
    errs = [ascii_(e.value) for e in at.exception]
    print("%-34s exceptions=%s metrics=%d dataframes=%d" % (label, errs or "none", len(at.metric), len(at.dataframe)))
    return not errs


t = time.time()
at = AppTest.from_file(str(root / "app.py"), default_timeout=120).run()
ok = report("initial render (%.1fs)" % (time.time() - t), at)
print("tabs:", [ascii_(x.label) for x in at.tabs])

# Buy / Sell planner: DCA mode (default), then Sell mode, then Sell by trimming to a target weight.
at.radio(key="plan_action").set_value("Sell / trim").run()
ok &= report("planner: sell / shares", at)
at.radio(key="sell_mode").set_value("Trim to % of wallet").run()
ok &= report("planner: sell / trim to target", at)
at.radio(key="plan_action").set_value("Buy more (DCA)").run()
ok &= report("planner: back to DCA", at)

# Allocation limit + portfolio sort + performance range.
at.number_input(key="pf_limit").set_value(10.0).run()
ok &= report("allocation limit = 10%", at)
at.radio(key="pf_sort").set_value("Symbol").run()
ok &= report("portfolio sort = Symbol", at)
at.radio(key="perf_range").set_value("1M").run()
ok &= report("performance range = 1M", at)

print("ALL OK" if ok else "FAILURES")
