"""Rate and size limit must persist across sessions. Runs on a THROWAWAY copy of the DB."""
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
dbmod.Store(tmp)  # makes sure the settings table exists in the copy
with sqlite3.connect(tmp) as _c:  # start from NO saved settings, whatever the real DB holds
    _c.execute("DELETE FROM settings")
from streamlit.testing.v1 import AppTest  # noqa: E402


def saved():
    return dict(sqlite3.connect(tmp).execute("select key, value from settings").fetchall())


# Session 1: defaults, then change both values.
a = AppTest.from_file(str(root / "app.py"), default_timeout=120).run()
assert not a.exception, [str(e.value) for e in a.exception]
print("session 1 defaults  -> rate", a.number_input(key="perf_rate").value, "| limit", a.number_input(key="pf_limit").value, "| saved:", saved())
assert a.number_input(key="perf_rate").value == 25.0 and saved() == {}

a.number_input(key="perf_rate").set_value(31.5).run()
a.number_input(key="pf_limit").set_value(12.0).run()
assert not a.exception, [str(e.value) for e in a.exception]
print("after editing       -> saved in DB:", saved())
assert saved() == {"deposit_rate": "31.5", "size_limit": "12.0"}

# Session 2: a completely fresh session must start from the saved values.
b = AppTest.from_file(str(root / "app.py"), default_timeout=120).run()
assert not b.exception, [str(e.value) for e in b.exception]
print("session 2 (fresh)   -> rate", b.number_input(key="perf_rate").value, "| limit", b.number_input(key="pf_limit").value)
assert b.number_input(key="perf_rate").value == 31.5
assert b.number_input(key="pf_limit").value == 12.0

# The saved rate must actually drive the benchmark text ("Same cash in a 31.5% deposit").
text = " ".join(m.value for m in b.markdown)
assert "31.5% deposit" in text, "saved rate not used by the deposit comparison"
print("deposit comparison uses the saved rate: yes")
print("\nSETTINGS PERSIST OK")
