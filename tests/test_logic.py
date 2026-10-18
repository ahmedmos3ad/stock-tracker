"""Run with:  python3 -m unittest discover -s tests -v   (no extra dependencies)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.actions import apply_corporate_actions, resolve_actions
from tracker.calculator import DEFAULT_FEE_RATE, estimate_fee, shares_to_trim, simulate_dca, simulate_sell
from tracker.performance import (
    cash_flows,
    deposit_equivalent,
    equity_curve,
    index_equivalent,
    xirr,
)
from tracker.portfolio import BUY, SELL, Dividend, Transaction, compute_positions


def T(sym, side, qty, price, date, fee=0.0):
    return Transaction(sym, side, qty, price, fee, date)


def pos(txns, prices=None, divs=()):
    return {p.symbol: p for p in compute_positions(txns, divs, prices or {})}


class XirrTests(unittest.TestCase):
    def test_simple_annual_return(self):
        r = xirr([("2024-01-01", -1000.0), ("2025-01-01", 1100.0)])
        self.assertAlmostEqual(r, 0.10, places=2)  # 366-day span -> ~9.97%

    def test_loss(self):
        r = xirr([("2024-01-01", -1000.0), ("2025-01-01", 900.0)])
        self.assertLess(r, 0)

    def test_needs_both_directions(self):
        self.assertIsNone(xirr([("2024-01-01", -1000.0)]))
        self.assertIsNone(xirr([]))

    def test_matches_known_multi_flow(self):
        # Two equal deposits a year apart, ending at 2310: solves to exactly 10%.
        r = xirr([("2023-01-01", -1000.0), ("2024-01-01", -1000.0), ("2025-01-01", 2310.0)])
        self.assertAlmostEqual(r, 0.10, delta=0.003)


class CorporateActionTests(unittest.TestCase):
    def test_bonus_keeps_cost_and_lowers_average(self):
        base = [T("AAA", BUY, 100, 10.0, "2024-01-01")]
        action = [{"id": 1, "symbol": "AAA", "date": "2024-06-01", "kind": "bonus", "new_shares": 1, "per_held": 10}]
        p = pos(apply_corporate_actions(base, action))["AAA"]
        self.assertEqual(p.quantity, 110)
        self.assertAlmostEqual(p.total_buy_cost, 1000.0)
        self.assertAlmostEqual(p.broker_avg, 1000 / 110)
        self.assertAlmostEqual(p.breakeven, 1000 / 110)

    def test_split_two_for_one(self):
        base = [T("AAA", BUY, 50, 20.0, "2024-01-01")]
        action = [{"id": 1, "symbol": "AAA", "date": "2024-03-01", "kind": "split", "new_shares": 1, "per_held": 1}]
        p = pos(apply_corporate_actions(base, action))["AAA"]
        self.assertEqual(p.quantity, 100)
        self.assertAlmostEqual(p.broker_avg, 10.0)

    def test_fractions_round_down_and_use_holding_on_date(self):
        base = [T("AAA", BUY, 25, 10.0, "2024-01-01"), T("AAA", BUY, 100, 10.0, "2024-09-01")]
        action = [{"id": 1, "symbol": "AAA", "date": "2024-06-01", "kind": "bonus", "new_shares": 1, "per_held": 10}]
        (r,) = resolve_actions(base, action)
        self.assertEqual((r["held"], r["extra"]), (25, 2))  # later buy not counted, 2.5 -> 2

    def test_bonus_on_bonus_compounds(self):
        base = [T("AAA", BUY, 100, 10.0, "2024-01-01")]
        actions = [
            {"id": 1, "symbol": "AAA", "date": "2024-02-01", "kind": "bonus", "new_shares": 1, "per_held": 10},
            {"id": 2, "symbol": "AAA", "date": "2024-03-01", "kind": "bonus", "new_shares": 1, "per_held": 10},
        ]
        self.assertEqual(pos(apply_corporate_actions(base, actions))["AAA"].quantity, 121)

    def test_other_symbols_untouched(self):
        base = [T("AAA", BUY, 100, 10.0, "2024-01-01"), T("BBB", BUY, 10, 5.0, "2024-01-01")]
        action = [{"id": 1, "symbol": "AAA", "date": "2024-02-01", "kind": "bonus", "new_shares": 1, "per_held": 10}]
        out = pos(apply_corporate_actions(base, action))
        self.assertEqual(out["BBB"].quantity, 10)

    def test_bonus_contributes_no_cash_flow(self):
        base = [T("AAA", BUY, 100, 10.0, "2024-01-01")]
        action = [{"id": 1, "symbol": "AAA", "date": "2024-06-01", "kind": "bonus", "new_shares": 1, "per_held": 10}]
        self.assertEqual(len(cash_flows(apply_corporate_actions(base, action))), 1)


class PlannerTests(unittest.TestCase):
    LEDGER = [
        T("AAA", BUY, 100, 10.0, "2024-01-01", 5.0),
        T("AAA", BUY, 50, 8.0, "2024-02-01", 5.0),
        T("AAA", SELL, 30, 12.0, "2024-03-01", 5.0),
    ]

    def test_dca_matches_real_engine(self):
        p = pos(self.LEDGER)["AAA"]
        r = simulate_dca(p.quantity, p.broker_avg, p.net_invested, 7.0, 40, fee=6.0)
        real = pos(self.LEDGER + [T("AAA", BUY, 40, 7.0, "2024-04-01", 6.0)])["AAA"]
        self.assertAlmostEqual(r.new_broker_avg, real.broker_avg)
        self.assertAlmostEqual(r.new_breakeven, real.breakeven)

    def test_sell_matches_real_engine(self):
        p = pos(self.LEDGER)["AAA"]
        r = simulate_sell(p.quantity, p.broker_avg, p.net_invested, 11.0, 40, fee=7.0)
        real = pos(self.LEDGER + [T("AAA", SELL, 40, 11.0, "2024-04-01", 7.0)])["AAA"]
        self.assertEqual(r.new_qty, real.quantity)
        self.assertAlmostEqual(r.realized_pnl, real.realized_pnl - p.realized_pnl)
        self.assertAlmostEqual(r.broker_avg, real.broker_avg)
        self.assertAlmostEqual(r.new_breakeven, real.breakeven)

    def test_full_sell_has_no_breakeven(self):
        r = simulate_sell(10, 5.0, 50.0, 6.0, 10)
        self.assertEqual(r.new_qty, 0)
        self.assertIsNone(r.new_breakeven)

    def test_sell_validation(self):
        with self.assertRaises(ValueError):
            simulate_sell(10, 5.0, 50.0, 6.0, 11)

    def test_trim_hits_target_weight(self):
        qty, price, others = 1000, 10.0, 30000.0  # position 10k of 40k = 25%
        s = shares_to_trim(qty, price, others, 20.0)

        def weight(q):
            return q * price / (others + q * price) * 100

        self.assertLessEqual(weight(qty - s), 20.0)
        self.assertGreater(weight(qty - s + 1), 20.0)  # one share fewer sold would overshoot

    def test_trim_nothing_when_already_under(self):
        self.assertEqual(shares_to_trim(100, 10.0, 1_000_000.0, 20.0), 0)


class FeeTests(unittest.TestCase):
    def test_default_rate_is_0_028_percent(self):
        self.assertAlmostEqual(DEFAULT_FEE_RATE, 0.00028)

    def test_estimate_fee(self):
        self.assertEqual(estimate_fee(103, 76.01), 2.19)   # 7,829.03 * 0.028%
        self.assertEqual(estimate_fee(1000, 10.0), 2.8)
        self.assertEqual(estimate_fee(0, 50.0), 0.0)
        self.assertEqual(estimate_fee(10, -5.0), 0.0)       # bad input never yields a negative fee


class AssetTypeTests(unittest.TestCase):
    def test_store_flag_defaults_to_stock_and_is_reversible(self):
        import tempfile
        from tracker.db import Store

        s = Store(Path(tempfile.mkdtemp()) / "a.db")
        self.assertEqual(s.fund_symbols(), [])
        s.set_fund("bal", True)
        s.set_fund("BWA", True)
        s.set_fund("BAL", True)  # idempotent
        self.assertEqual(s.fund_symbols(), ["BAL", "BWA"])
        s.set_fund("BAL", False)  # back to stock
        self.assertEqual(s.fund_symbols(), ["BWA"])
        s.set_fund("NEVER", False)  # unknown symbol: no error
        self.assertEqual(s.fund_symbols(), ["BWA"])

    def test_existing_database_upgrades_in_place(self):
        import sqlite3, tempfile
        from tracker.db import Store

        path = Path(tempfile.mkdtemp()) / "old.db"
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, side TEXT NOT NULL, "
                  "quantity REAL NOT NULL, price REAL NOT NULL, fee REAL NOT NULL DEFAULT 0, date TEXT NOT NULL)")
        c.execute("INSERT INTO transactions (symbol, side, quantity, price, fee, date) VALUES ('AAA','buy',10,5,0,'2026-01-01')")
        c.commit(); c.close()
        s = Store(path)
        self.assertEqual(s.fund_symbols(), [])
        self.assertEqual(len(s.list_transactions()), 1)  # data untouched

    def test_sync_targets_exclude_funds_and_keep_order(self):
        from tracker.assets import fund_list, stock_symbols

        syms = ["ABUK", "BAL", "COMI", "BWA"]
        self.assertEqual(stock_symbols(syms, ["BAL", "BWA"]), ["ABUK", "COMI"])
        self.assertEqual(fund_list(syms, ["BAL", "BWA"]), ["BAL", "BWA"])
        self.assertEqual(stock_symbols(syms, []), syms)

    def test_stale_navs(self):
        from datetime import date
        from tracker.assets import stale_navs

        entries = {
            "BAL": {"updated_at": "2026-10-17T10:00:00+03:00"},   # 1 day old: fine
            "BWA": {"updated_at": "2026-10-01T10:00:00+03:00"},   # 17 days: stale
            "BAD": {"updated_at": "garbage"},                      # unreadable: treated as never
        }
        out = stale_navs(["BAL", "BWA", "CI30", "BAD"], entries, date(2026, 10, 18))
        self.assertEqual(out, [("BWA", 17), ("CI30", None), ("BAD", None)])
        self.assertEqual(stale_navs(["BAL"], entries, date(2026, 10, 18), max_age_days=0), [("BAL", 1)])


class SettingsTests(unittest.TestCase):
    def test_roundtrip_overwrite_and_persistence(self):
        import tempfile
        from tracker.db import Store

        path = Path(tempfile.mkdtemp()) / "s.db"
        s = Store(path)
        self.assertEqual(s.settings(), {})
        s.set_setting("deposit_rate", 27.5)
        s.set_setting("size_limit", 15)
        s.set_setting("deposit_rate", 30.0)  # overwrite, not duplicate
        self.assertEqual(s.settings(), {"deposit_rate": "30.0", "size_limit": "15"})
        self.assertEqual(Store(path).settings()["deposit_rate"], "30.0")  # survives a new connection


class AppStructureTests(unittest.TestCase):
    """Static guards on app.py (parsed, never imported, so no Streamlit needed)."""

    @classmethod
    def setUpClass(cls):
        import ast

        cls.src = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.src)
        cls.funcs = {n.name: n for n in ast.walk(cls.tree) if isinstance(n, ast.FunctionDef)}

    @staticmethod
    def _names(node):
        import ast

        return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

    def test_dialogs_do_not_read_late_bound_globals(self):
        # Streamlit re-runs a dialog on its own, without re-executing the script. A dialog that reads a
        # module global assigned later in the script (like the fund list) crashes with NameError.
        for fn in ("_company_picker", "transaction_dialog", "dividend_dialog"):
            self.assertNotIn("fund_symbols", self._names(self.funcs[fn]), f"{fn} reads the global fund_symbols")

    def test_every_eodhd_call_is_guarded_against_funds(self):
        import ast

        callers = {}
        for name, fn in self.funcs.items():
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in (
                    "fetch_latest_eodhd_close", "fetch_history"
                ):
                    callers.setdefault(n.func.id, set()).add(name)
        # each EODHD endpoint is reachable from exactly one wrapper...
        self.assertEqual(callers["fetch_latest_eodhd_close"], {"_fetch_close"})
        self.assertEqual(callers["fetch_history"], {"_fetch_hist"})
        # ...and that wrapper re-reads the fund flags from the database before calling out.
        for wrapper in ("_fetch_close", "_fetch_hist"):
            self.assertIn("live_funds", self._names(self.funcs[wrapper]), f"{wrapper} has no fund guard")
        # nothing else in the app may call those endpoints directly (e.g. via a lambda at module level)
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in callers:
                owner = next((nm for nm, f in self.funcs.items() if n in ast.walk(f)), None)
                self.assertIn(owner, {"_fetch_close", "_fetch_hist"}, f"unguarded call to {n.func.id}")


class CurveAndBenchmarkTests(unittest.TestCase):
    def test_curve_value_and_contributed(self):
        ledger = [T("AAA", BUY, 10, 10.0, "2024-01-01", 2.0), T("AAA", SELL, 4, 15.0, "2024-01-04", 1.0)]
        hist = {"AAA": [("2024-01-01", 10.0), ("2024-01-02", 12.0), ("2024-01-05", 20.0)]}
        c = {r["date"]: r for r in equity_curve(ledger, hist, end="2024-01-05")}
        self.assertAlmostEqual(c["2024-01-01"]["value"], 100.0)
        self.assertAlmostEqual(c["2024-01-03"]["value"], 120.0)  # carries last close forward
        self.assertAlmostEqual(c["2024-01-01"]["contributed"], 102.0)
        self.assertAlmostEqual(c["2024-01-04"]["contributed"], 102.0 - (4 * 15.0 - 1.0))
        self.assertAlmostEqual(c["2024-01-05"]["value"], 6 * 20.0)

    def test_curve_falls_back_to_trade_price(self):
        ledger = [T("AAA", BUY, 10, 10.0, "2024-01-01")]
        c = equity_curve(ledger, {}, end="2024-01-02")
        self.assertAlmostEqual(c[-1]["value"], 100.0)

    def test_index_equivalent_of_buy_and_hold(self):
        flows = [("2024-01-01", -1000.0)]
        idx = [("2024-01-01", 100.0), ("2025-01-01", 120.0)]
        r = index_equivalent(flows, idx, "2025-01-01")
        self.assertAlmostEqual(r["value"], 1200.0)
        self.assertAlmostEqual(r["xirr"], 0.20, delta=0.01)

    def test_deposit_equivalent(self):
        r = deposit_equivalent([("2024-01-01", -1000.0)], 0.25, "2025-01-01")
        self.assertAlmostEqual(r["value"], 1250.0, delta=2.0)


class HistoryParsingTests(unittest.TestCase):
    def test_codes(self):
        from tracker.history import EGX30_SYMBOL, eodhd_code

        self.assertEqual(eodhd_code("comi"), "COMI.EGX")
        self.assertEqual(eodhd_code("COMI.CA"), "COMI.EGX")
        self.assertEqual(eodhd_code(EGX30_SYMBOL), "EGX30.INDX")

    def test_parse_eod_uses_raw_close_and_skips_bad_rows(self):
        from tracker.history import parse_eod

        payload = [
            {"date": "2026-06-11", "open": 80, "high": 83, "low": 79, "close": 82.0, "adjusted_close": 41.0, "volume": 10},
            {"date": "2026-06-10", "close": 81.5, "adjusted_close": 40.7},
            {"date": "2026-06-12", "close": 0},        # no trade -> ignored
            {"date": "2026-06-13", "close": None},     # malformed -> ignored
            {"close": 5.0},                            # no date -> ignored
        ]
        self.assertEqual(parse_eod(payload), [("2026-06-10", 81.5), ("2026-06-11", 82.0)])
        self.assertEqual(parse_eod({"error": "x"}), [])

    def test_fetch_requires_key(self):
        from tracker.history import fetch_history

        self.assertEqual(fetch_history("COMI", "2026-06-01", ""), (None, "no API key"))


class MigrationTests(unittest.TestCase):
    def test_legacy_price_history_is_copied_not_modified(self):
        import sqlite3, tempfile
        from tracker.db import Store

        path = Path(tempfile.mkdtemp()) / "legacy.db"
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE price_history (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
                  "current_price REAL NOT NULL, updated_at TEXT NOT NULL)")
        c.executemany("INSERT INTO price_history (symbol, current_price, updated_at) VALUES (?, ?, ?)", [
            ("AAA", 10.0, "2026-06-11T00:31:57+03:00"),
            ("AAA", 11.0, "2026-06-11T23:59:59+03:00"),  # same day: the later row wins
            ("BBB", 5.0, "2026-06-12T10:00:00+03:00"),
        ])
        c.commit(); c.close()

        s = Store(path)
        self.assertEqual(s.price_history(), {"AAA": [("2026-06-11", 11.0)], "BBB": [("2026-06-12", 5.0)]})
        Store(path)  # reopening is idempotent
        legacy = sqlite3.connect(path).execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
        self.assertEqual(legacy, 3)  # legacy table untouched

    def test_set_price_records_a_daily_point(self):
        import tempfile
        from tracker.db import Store

        s = Store(Path(tempfile.mkdtemp()) / "new.db")
        s.set_price("AAA", 10.0, "2026-07-01T09:00:00+03:00")
        s.set_price("AAA", 12.0, "2026-07-01T15:00:00+03:00")
        s.set_price("AAA", 13.0, "2026-07-02T15:00:00+03:00")
        self.assertEqual(s.price_history()["AAA"], [("2026-07-01", 12.0), ("2026-07-02", 13.0)])
        self.assertEqual(s.prices()["AAA"], 13.0)


class RealLedgerTests(unittest.TestCase):
    """Cross-checks against the actual database when it exists (read-only copy)."""

    def test_curve_end_matches_engine(self):
        db = Path(__file__).resolve().parent.parent / "portfolio.db"
        if not db.exists():
            self.skipTest("no portfolio.db")
        import shutil, tempfile
        from tracker.db import Store

        tmp = Path(tempfile.mkdtemp()) / "copy.db"
        shutil.copy(db, tmp)
        s = Store(tmp)
        txns = apply_corporate_actions(s.transactions(), s.list_corporate_actions())
        prices = s.prices()
        positions = compute_positions(txns, (), prices)
        curve = equity_curve(txns, {}, end=max(t.date for t in txns))
        # net cash contributed must equal the engine's net invested (ex-dividends).
        self.assertAlmostEqual(curve[-1]["contributed"], sum(p.net_invested for p in positions), places=4)


if __name__ == "__main__":
    unittest.main()
