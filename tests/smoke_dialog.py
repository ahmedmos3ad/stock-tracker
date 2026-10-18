"""Headless check that the Add-transaction dialog opens with the auto-fee UI (throwaway DB copy).

Limitation: AppTest cannot simulate typing *inside* a dialog (it reruns the whole script, which never
re-opens the dialog), so saving is covered by unit tests of estimate_fee plus manual use.
"""
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
sys.stdout.reconfigure(encoding="utf-8")

# Point the app's Store at a copy so the real portfolio.db is never touched.
tmp = Path(tempfile.mkdtemp()) / "copy.db"
shutil.copyfile(root / "portfolio.db", tmp)
import tracker.db as dbmod  # noqa: E402

dbmod.Store.__init__.__defaults__ = (tmp,)

from streamlit.testing.v1 import AppTest  # noqa: E402

at = AppTest.from_file(str(root / "app.py"), default_timeout=120).run()
assert not at.exception, [e.value for e in at.exception]
next(b for b in at.button if "Add transaction" in b.label).click().run()
assert not at.exception, [str(e.value) for e in at.exception]

fee_input = next(n for n in at.number_input if n.key and n.key.endswith("_fee") and n.key.startswith("txn"))
assert fee_input.value is None and fee_input.placeholder == "auto", (fee_input.value, fee_input.placeholder)
assert any("Fee left empty" in c.value and "0.028%" in c.value for c in at.caption)
rows = sqlite3.connect(tmp).execute("select count(*) from transactions").fetchone()[0]
print("DIALOG OK: opens cleanly, fee field is empty with placeholder 'auto', caption explains 0.028%%. rows=%d" % rows)
