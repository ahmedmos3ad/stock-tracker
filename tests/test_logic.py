"""Run with:  python3 -m unittest discover -s tests -v   (no extra dependencies)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker.actions import apply_corporate_actions, resolve_actions
from tracker.calculator import (
    DEFAULT_FEE_RATE, estimate_fee, fee_for_trade, shares_to_trim, simulate_dca, simulate_sell,
)
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
        for fn in (
            "_company_picker", "transaction_dialog", "dividend_dialog", "sell_planner", "dca_tab", "net_worth_dialog"
        ):
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

    def test_market_quotes_only_fetched_by_the_net_worth_refresh(self):
        import ast

        owners = set()
        for name, fn in self.funcs.items():
            for n in ast.walk(fn):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "fetch_realtime_price":
                    owners.add(name)
        self.assertEqual(owners, {"_refresh_market_prices"})


class FundFeeTests(unittest.TestCase):
    def test_fund_trades_default_to_zero_fee(self):
        self.assertEqual(fee_for_trade(1000, 10.0, is_fund=True), 0.0)
        self.assertEqual(fee_for_trade(1000, 10.0, is_fund=False), 2.8)
        self.assertEqual(fee_for_trade(1000, 10.0), 2.8)  # stock is the default


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


class NetWorthTests(unittest.TestCase):
    def test_effective_gold_prices_derives_missing_karat(self):
        from tracker.networth import effective_gold_prices

        self.assertEqual(effective_gold_prices(4800.0, 0.0), (4800.0, 4200.0))
        self.assertEqual(effective_gold_prices(0.0, 4200.0), (4800.0, 4200.0))
        self.assertEqual(effective_gold_prices(4800.0, 4215.0), (4800.0, 4215.0))
        self.assertEqual(effective_gold_prices(0.0, 0.0), (0.0, 0.0))

    def test_compute_net_worth_across_all_asset_classes(self):
        from tracker.networth import (
            CAT_CASH, CAT_CD, CAT_FUND, CAT_FX, CAT_GOLD, NetWorthItem, compute_net_worth,
        )

        txns = [
            T("ABUK", BUY, 100, 90.0, "2026-01-01"),
            T("NBE05", BUY, 1000, 50.0, "2026-01-01"),
        ]
        positions = compute_positions(txns, (), {"ABUK": 100.0, "NBE05": 55.0})
        items = [
            NetWorthItem(1, CAT_GOLD, "Gold Pound (8g · 21K)", quantity=2.0, unit_value=25.0, karat=21, grams=8.0),
            NetWorthItem(2, CAT_GOLD, "10g Bar (24K)", quantity=1.0, unit_value=30.0, karat=24, grams=10.0),
            NetWorthItem(3, CAT_FX, "USD", quantity=1000.0, unit_value=50.0),
            NetWorthItem(4, CAT_CD, "NBE 27% CD", quantity=200000.0, unit_value=27.0),
            NetWorthItem(5, CAT_CASH, "Savings", quantity=10000.0, unit_value=0.0),
            NetWorthItem(6, CAT_FUND, "Beltone BMM (direct)", quantity=30000.0, unit_value=0.0, grams=20.0),
            NetWorthItem(7, CAT_FUND, "Azimut Fund (units)", quantity=500.0, unit_value=40.0, grams=22.0),
        ]
        # 24K = 4800 -> derived 21K = 4200
        # Gold Pound:   2 * 8g * (4200 + 25) = 67,600
        # 10g Bar:      1 * 10g * (4800 + 30) = 48,300 -> total gold = 115,900
        # FX:           1000 * 50 = 50,000
        # CD+Cash:      200,000 + 10,000 = 210,000
        # Equities:     ABUK (100 * 100 = 10,000) + NBE05 stock fund (1000 * 55 = 55,000) = 65,000
        # Mutual Funds: 30,000 + (500 * 40 = 20,000) = 50,000
        # Fund income:  30,000 * 20% (6,000) + 20,000 * 22% (4,400) = 10,400
        nw = compute_net_worth(positions, ["NBE05"], items, price_24k=4800.0, price_21k=0.0)
        self.assertAlmostEqual(nw["by_key"]["stocks"], 65000.0)
        self.assertAlmostEqual(nw["stocks_only_egp"], 10000.0)
        self.assertAlmostEqual(nw["stock_funds_egp"], 55000.0)
        self.assertAlmostEqual(nw["by_key"]["funds"], 50000.0)
        self.assertAlmostEqual(nw["by_key"]["gold"], 115900.0)
        self.assertAlmostEqual(nw["by_key"]["fx"], 50000.0)
        self.assertAlmostEqual(nw["by_key"]["cd_cash"], 210000.0)
        self.assertAlmostEqual(nw["total"], 490900.0)
        self.assertAlmostEqual(nw["gold_grams_21k"], 16.0)
        self.assertAlmostEqual(nw["gold_grams_24k"], 10.0)
        self.assertAlmostEqual(nw["gold_total_grams"], 26.0)
        self.assertAlmostEqual(nw["annual_cd_income"], 54000.0)
        self.assertAlmostEqual(nw["annual_fund_income"], 10400.0)
        self.assertAlmostEqual(sum(s["pct"] for s in nw["slices"]), 100.0)

    def test_store_crud_for_net_worth_items(self):
        import tempfile
        from tracker.db import Store
        from tracker.networth import CAT_CD, CAT_FUND, CAT_FX, CAT_GOLD

        s = Store(Path(tempfile.mkdtemp()) / "nw.db")
        self.assertEqual(s.list_net_worth_items(), [])
        gid = s.add_net_worth_item(CAT_GOLD, "Gold Pound (8g · 21K)", 3.0, 20.0, karat=21, grams=8.0, notes="BTC")
        fid = s.add_net_worth_item(CAT_FX, "USD", 1500.0, 49.5, notes="CIB")
        cid = s.add_net_worth_item(CAT_CD, "NBE CD", 100000.0, 27.0)
        mid = s.add_net_worth_item(CAT_FUND, "BMM", 50000.0, 0.0)
        items = s.net_worth_items()
        self.assertEqual(len(items), 4)

        s.update_net_worth_item(fid, CAT_FX, "USD", 2000.0, 50.0, notes="CIB updated")
        by_id = {it.id: it for it in s.net_worth_items()}
        self.assertAlmostEqual(by_id[fid].quantity, 2000.0)
        self.assertAlmostEqual(by_id[fid].unit_value, 50.0)
        self.assertEqual(by_id[fid].notes, "CIB updated")

        s.delete_net_worth_item(cid)
        self.assertEqual({it.id for it in s.net_worth_items()}, {gid, fid, mid})

    def test_usd_certificate_and_currency_exposure_breakdown(self):
        from tracker.networth import CAT_CD, CAT_FX, CAT_US_STOCK, NetWorthItem, compute_net_worth

        txns = [T("ABUK", BUY, 1000, 100.0, "2026-01-01")]  # 100,000 EGP in stocks
        positions = compute_positions(txns, (), {"ABUK": 100.0})
        items = [
            NetWorthItem(1, CAT_FX, "USD", quantity=1000.0, unit_value=50.0, currency="USD", fx_rate=50.0),
            NetWorthItem(2, CAT_CD, "NBE USD CD", quantity=3000.0, unit_value=6.0, currency="USD", fx_rate=50.0),
            NetWorthItem(3, CAT_CD, "BM EGP CD", quantity=100000.0, unit_value=27.0, currency="EGP", fx_rate=1.0),
            NetWorthItem(4, CAT_US_STOCK, "PCOR", quantity=20.0, unit_value=50.0, currency="USD", fx_rate=50.0),
        ]
        # PCOR: 20 shares * $50 = $1,000 USD = 50,000 EGP
        # Total = 100k (EGX) + 50k (PCOR) + 50k (USD cash) + 150k (USD CD) + 100k (EGP CD) = 450,000 EGP
        # Total USD = 1000 (cash) + 3000 (CD) + 1000 (PCOR) = 5000 USD = 250,000 EGP
        # Annual CD income = 3000 * 6% * 50 (9,000 EGP) + 100,000 * 27% (27,000 EGP) = 36,000 EGP
        nw = compute_net_worth(positions, [], items, usd_egp_rate=50.0)
        self.assertAlmostEqual(nw["total"], 450000.0)
        self.assertAlmostEqual(nw["by_key"]["us_stocks"], 50000.0)
        self.assertAlmostEqual(nw["by_key"]["cd_cash"], 250000.0)
        self.assertAlmostEqual(nw["usd_stock_usd"], 1000.0)
        self.assertAlmostEqual(nw["usd_cash_usd"], 1000.0)
        self.assertAlmostEqual(nw["usd_cd_usd"], 3000.0)
        self.assertAlmostEqual(nw["usd_total_usd"], 5000.0)
        self.assertAlmostEqual(nw["usd_total_egp"], 250000.0)
        self.assertAlmostEqual(nw["usd_pct"], 250000.0 / 450000.0 * 100.0)
        self.assertAlmostEqual(nw["egp_exposure_egp"], 200000.0)
        self.assertAlmostEqual(nw["egp_pct"], 200000.0 / 450000.0 * 100.0)
        self.assertAlmostEqual(nw["annual_cd_income"], 36000.0)

    def test_net_worth_snapshots_crud_series_and_chart(self):
        import tempfile
        from tracker.db import Store
        from tracker.networth import (
            CAT_CD,
            CAT_US_STOCK,
            NetWorthItem,
            build_snapshot_series,
            compute_net_worth,
            nw_chart_html,
            snapshot_from_nw,
        )

        s = Store(Path(tempfile.mkdtemp()) / "nw_snap.db")
        self.assertEqual(s.list_net_worth_snapshots(), [])

        txns = [T("ABUK", BUY, 1000, 100.0, "2026-01-01")]
        positions = compute_positions(txns, (), {"ABUK": 100.0})
        items = [
            NetWorthItem(1, CAT_US_STOCK, "PCOR", quantity=20.0, unit_value=50.0, currency="USD", fx_rate=50.0),
            NetWorthItem(2, CAT_CD, "BM EGP CD", quantity=100000.0, unit_value=27.0, currency="EGP", fx_rate=1.0),
        ]
        nw_sep = compute_net_worth(positions, [], items, usd_egp_rate=50.0)
        snap_sep = snapshot_from_nw("2026-09-30", nw_sep)
        self.assertEqual(snap_sep["date"], "2026-09-30")
        self.assertAlmostEqual(snap_sep["total_egp"], 250000.0)
        self.assertAlmostEqual(snap_sep["stocks_egp"], 100000.0)
        self.assertAlmostEqual(snap_sep["us_stocks_egp"], 50000.0)
        self.assertAlmostEqual(snap_sep["cd_cash_egp"], 100000.0)

        s.upsert_net_worth_snapshot(snap_sep)
        # Same-day update overwrites that day's snapshot
        snap_sep_updated = dict(snap_sep, gold_egp=25000.0, total_egp=275000.0)
        s.upsert_net_worth_snapshot(snap_sep_updated)
        saved = s.list_net_worth_snapshots()
        self.assertEqual(len(saved), 1)
        self.assertAlmostEqual(saved[0]["total_egp"], 275000.0)
        self.assertAlmostEqual(saved[0]["gold_egp"], 25000.0)

        # Live Net Worth on a later day is appended/merged into the series
        nw_oct = compute_net_worth(
            compute_positions(txns, (), {"ABUK": 120.0}),
            [],
            items,
            usd_egp_rate=50.0,
        )
        series = build_snapshot_series(saved, "2026-10-22", nw_oct)
        self.assertEqual([pt["date"] for pt in series], ["2026-09-30", "2026-10-22"])
        self.assertAlmostEqual(series[-1]["total_egp"], 270000.0)
        self.assertAlmostEqual(series[-1]["stocks_egp"], 120000.0)

        html = nw_chart_html(series, "EGP")
        self.assertIn("Net Worth Over Time", html)
        self.assertIn("Stacked Breakdown", html)

        s.delete_net_worth_snapshot("2026-09-30")
        self.assertEqual(s.list_net_worth_snapshots(), [])


class NetWorthManagementTests(unittest.TestCase):
    """Liabilities, snapshot rates, and the nw_insights helpers."""

    def _items(self):
        from tracker.networth import (
            CAT_CASH, CAT_CD, CAT_FUND, CAT_FX, CAT_GOLD, CAT_LIABILITY, CAT_US_STOCK, NetWorthItem,
        )

        return [
            NetWorthItem(1, CAT_US_STOCK, "PCOR", quantity=100.0, unit_value=50.0, currency="USD", fx_rate=50.0,
                         updated_at="2026-10-01T10:00:00+03:00"),
            NetWorthItem(2, CAT_US_STOCK, "PCOR (ESPP)", quantity=20.0, unit_value=50.0, currency="USD", fx_rate=50.0,
                         updated_at="2026-10-24T10:00:00+03:00"),
            NetWorthItem(3, CAT_CD, "USD CD", quantity=1000.0, unit_value=5.0, currency="USD", fx_rate=50.0,
                         maturity_date="2026-11-10"),
            NetWorthItem(4, CAT_CD, "EGP CD", quantity=100000.0, unit_value=20.0, maturity_date="2027-06-01"),
            NetWorthItem(5, CAT_CASH, "Savings", quantity=50000.0),
            NetWorthItem(6, CAT_FX, "USD", quantity=200.0, currency="USD", fx_rate=50.0),
            NetWorthItem(7, CAT_FUND, "MMF", quantity=40000.0, grams=18.0, updated_at="2026-10-20"),
            NetWorthItem(8, CAT_GOLD, "10g Bar (24K)", quantity=1.0, karat=24, grams=10.0),
            NetWorthItem(9, CAT_LIABILITY, "Car loan", quantity=60000.0, unit_value=20.0, monthly_amount=5000.0,
                         maturity_date="2027-12-01", updated_at="2026-10-25"),
            NetWorthItem(10, CAT_LIABILITY, "USD card", quantity=100.0, currency="USD", fx_rate=50.0,
                         updated_at="2026-10-25"),
        ]

    def _nw(self):
        from tracker.networth import compute_net_worth

        positions = compute_positions(
            [T("ABUK", BUY, 1000, 100.0, "2026-01-01"), T("NBE05", BUY, 100, 100.0, "2026-01-01")],
            (), {"ABUK": 150.0, "NBE05": 100.0},
        )
        return positions, compute_net_worth(positions, ["NBE05"], self._items(), 5000.0, 0.0, 50.0)

    def test_liabilities_are_netted_and_offset_their_currency(self):
        _, nw = self._nw()
        # Assets: EGX 150k+10k, PCOR 120sh*$50*50=300k, USD CD 50k, EGP CD 100k, cash 50k, FX 10k,
        #         MMF 40k, gold 10g*5000=50k  -> 760k gross.  Debts: 60k EGP + $100 (5k) = 65k.
        self.assertAlmostEqual(nw["gross_assets"], 760000.0)
        self.assertAlmostEqual(nw["liabilities_egp"], 65000.0)
        self.assertAlmostEqual(nw["total"], 695000.0)
        self.assertAlmostEqual(sum(s["pct"] for s in nw["slices"]), 100.0)  # shares of gross assets
        self.assertAlmostEqual(nw["usd_total_usd"], 6000.0 + 1000.0 + 200.0 - 100.0)
        self.assertAlmostEqual(nw["usd_total_egp"], 7100.0 * 50.0)
        # exposure buckets are shares of NET worth and must add up to 100%
        self.assertAlmostEqual(nw["egp_pct"] + nw["usd_pct"] + nw["gold_pct"] + nw["other_fx_pct"], 100.0)
        self.assertAlmostEqual(nw["annual_interest_cost"], 60000.0 * 0.20)
        self.assertAlmostEqual(nw["monthly_debt_payments"], 5000.0)
        self.assertAlmostEqual(nw["cd_egp"], 150000.0)
        self.assertAlmostEqual(nw["cash_egp"], 50000.0)
        self.assertAlmostEqual(nw["usd_egp_rate"], 50.0)
        self.assertAlmostEqual(nw["gold_24k_price"], 5000.0)

    def test_snapshot_records_rates_and_series_fills_missing_rate(self):
        from tracker.networth import build_snapshot_series, snapshot_from_nw

        _, nw = self._nw()
        snap = snapshot_from_nw("2026-10-25", nw)
        self.assertAlmostEqual(snap["usd_egp_rate"], 50.0)
        self.assertAlmostEqual(snap["liabilities_egp"], 65000.0)
        self.assertAlmostEqual(snap["usd_total_usd"], 7100.0)
        old = {"date": "2026-09-01", "total_egp": 500000.0, "usd_total_egp": 240000.0}  # pre-upgrade row
        series = build_snapshot_series([old], "2026-10-25", nw)
        self.assertTrue(series[0]["rate_estimated"])
        self.assertAlmostEqual(series[0]["usd_egp_rate"], 50.0)  # nearest known rate
        self.assertAlmostEqual(series[0]["usd_total_usd"], 4800.0)
        self.assertFalse(series[1]["rate_estimated"])

    def test_store_migrates_old_schema_and_roundtrips_new_fields(self):
        import sqlite3
        import tempfile
        from tracker.db import Store

        path = Path(tempfile.mkdtemp()) / "old.db"
        with sqlite3.connect(path) as c:
            c.executescript(
                """
                CREATE TABLE net_worth_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL CHECK (category IN ('gold','fx','cd','cash','fund','us_stock')),
                    name TEXT NOT NULL, quantity REAL NOT NULL, unit_value REAL NOT NULL DEFAULT 0,
                    karat INTEGER NOT NULL DEFAULT 24, grams REAL NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'EGP', fx_rate REAL NOT NULL DEFAULT 1.0,
                    notes TEXT NOT NULL DEFAULT '');
                INSERT INTO net_worth_items (category, name, quantity, unit_value) VALUES ('cd', 'Old CD', 1000, 20);
                CREATE TABLE net_worth_snapshots (date TEXT PRIMARY KEY, total_egp REAL NOT NULL,
                    stocks_egp REAL NOT NULL DEFAULT 0, us_stocks_egp REAL NOT NULL DEFAULT 0,
                    funds_egp REAL NOT NULL DEFAULT 0, gold_egp REAL NOT NULL DEFAULT 0,
                    fx_egp REAL NOT NULL DEFAULT 0, cd_cash_egp REAL NOT NULL DEFAULT 0,
                    usd_total_egp REAL NOT NULL DEFAULT 0);
                INSERT INTO net_worth_snapshots (date, total_egp) VALUES ('2026-10-01', 1000);
                """
            )
        s = Store(path)
        (old,) = s.net_worth_items()
        self.assertEqual((old.name, old.quantity, old.maturity_date), ("Old CD", 1000.0, ""))
        self.assertTrue(old.updated_at)  # freshness clock started at migration
        lid = s.add_net_worth_item("liability", "Loan", 5000.0, 18.0, maturity_date="2027-01-31",
                                   monthly_amount=500.0, updated_at="2026-10-25T09:00:00+03:00")
        loan = {it.id: it for it in s.net_worth_items()}[lid]
        self.assertEqual(loan.maturity_date, "2027-01-31")
        self.assertAlmostEqual(loan.monthly_amount, 500.0)
        s.set_net_worth_item_price(lid, 19.0, "2026-10-26T09:00:00+03:00")
        loan = {it.id: it for it in s.net_worth_items()}[lid]
        self.assertEqual((loan.unit_value, loan.updated_at), (19.0, "2026-10-26T09:00:00+03:00"))
        self.assertEqual(s.list_net_worth_snapshots()[0]["usd_egp_rate"], 0.0)
        s.upsert_net_worth_snapshot({"date": "2026-10-01", "total_egp": 900.0, "liabilities_egp": 100.0,
                                     "usd_egp_rate": 48.5, "usd_total_usd": 3.0, "gold_24k_price": 4000.0})
        row = s.list_net_worth_snapshots()[0]
        self.assertEqual((row["total_egp"], row["liabilities_egp"], row["usd_egp_rate"]), (900.0, 100.0, 48.5))
        Store(path)  # re-opening an upgraded DB is a no-op

    def test_stale_inputs(self):
        from datetime import date
        from tracker.nw_insights import stale_inputs

        settings = {"usd_egp_rate_updated_at": "2026-10-24T10:00:00+03:00"}  # gold price never stamped
        out = {r["label"]: r["age"] for r in stale_inputs(settings, self._items(), date(2026, 10, 25))}
        self.assertEqual(out, {"Gold price": None, "PCOR share price": 24, "Savings balance": None})

    def test_maturities_liquidity_and_emergency_fund(self):
        from datetime import date
        from tracker.nw_insights import (
            cds_without_maturity, emergency_fund_months, liquidity_breakdown, maturity_schedule,
        )

        _, nw = self._nw()
        sched = maturity_schedule(self._items(), date(2026, 10, 25), 5000.0, 0.0, 50.0)
        self.assertEqual([(r["item"].name, r["days_left"]) for r in sched],
                         [("USD CD", 16), ("EGP CD", 219), ("Car loan", 402)])
        self.assertAlmostEqual(sched[0]["value_egp"], 50000.0)
        self.assertEqual(cds_without_maturity(self._items()), [])
        tiers = {t["key"]: t["value"] for t in liquidity_breakdown(nw)}
        self.assertEqual(tiers, {"cash": 60000.0, "funds": 40000.0, "market": 510000.0, "locked": 150000.0})
        self.assertAlmostEqual(emergency_fund_months(nw, 20000.0), 5.0)
        self.assertIsNone(emergency_fund_months(nw, 0.0))

    def test_passive_income_and_trailing_dividends(self):
        from datetime import date
        from tracker.nw_insights import passive_income, trailing_dividends

        _, nw = self._nw()
        divs = [{"amount": 1200.0, "date": "2026-05-01"}, {"amount": 999.0, "date": "2025-01-01"}]
        d12 = trailing_dividends(divs, date(2026, 10, 25))
        self.assertAlmostEqual(d12, 1200.0)
        inc = passive_income(nw, d12, monthly_expenses=10000.0)
        # CDs: $1000*5%*50 + 100000*20% = 22,500/yr; fund 40000*18% = 7,200/yr; debt interest 12,000/yr
        self.assertAlmostEqual(inc["cd_monthly"], 22500.0 / 12)
        self.assertAlmostEqual(inc["fund_monthly"], 600.0)
        self.assertAlmostEqual(inc["dividend_monthly"], 100.0)
        self.assertAlmostEqual(inc["interest_monthly"], 1000.0)
        self.assertAlmostEqual(inc["net_monthly"], 22500.0 / 12 + 600.0 + 100.0 - 1000.0)
        self.assertAlmostEqual(inc["expense_coverage_pct"], inc["net_monthly"] / 100.0)

    def test_concentration_groups_tickers_and_skips_funds(self):
        from tracker.nw_insights import holdings_concentration, us_ticker

        self.assertEqual(us_ticker("PCOR (ESPP)"), "PCOR")
        positions, nw = self._nw()
        rows = holdings_concentration(positions, ["NBE05"], self._items(), nw, 50.0)
        self.assertEqual([r["name"] for r in rows], ["PCOR", "ABUK"])  # NBE05 is a fund -> excluded
        self.assertAlmostEqual(rows[0]["value"], 300000.0)
        self.assertAlmostEqual(rows[0]["pct"], 300000.0 / 760000.0 * 100.0)

    def test_allocation_drift_and_usd_gap(self):
        from tracker.nw_insights import allocation_drift, usd_target_gap

        _, nw = self._nw()
        rows = {r["key"]: r for r in allocation_drift(nw, {"gold": 10.0})}
        self.assertIsNone(rows["stocks"]["target_pct"])
        self.assertAlmostEqual(rows["gold"]["move_egp"], 76000.0 - 50000.0)
        gap = usd_target_gap(nw, 60.0)
        self.assertAlmostEqual(gap["move_egp"], 0.6 * 695000.0 - 355000.0)
        self.assertAlmostEqual(gap["move_usd"], gap["move_egp"] / 50.0)

    def test_change_breakdown_adds_up(self):
        from tracker.nw_insights import change_breakdown, egx_net_invested, real_change_pct

        txns = [T("ABUK", BUY, 100, 10.0, "2026-10-05", fee=1.0), T("ABUK", SELL, 50, 12.0, "2026-10-06", fee=1.0),
                T("ABUK", BUY, 1, 1.0, "2026-09-01")]
        invested = egx_net_invested(txns, "2026-10-01", "2026-10-25")
        self.assertAlmostEqual(invested, 1001.0 - 599.0)
        prev = {"total_egp": 1000000.0, "stocks_egp": 200000.0, "gold_egp": 100000.0, "usd_total_egp": 500000.0,
                "usd_egp_rate": 50.0, "gold_24k_price": 5000.0}
        cur = {"total_egp": 1100000.0, "stocks_egp": 210000.0, "gold_egp": 110000.0, "usd_total_egp": 561000.0,
               "usd_total_usd": 11000.0, "usd_egp_rate": 51.0, "gold_24k_price": 5500.0}
        bd = change_breakdown(prev, cur, invested)
        self.assertAlmostEqual(bd["usd_rate"], 10000.0 * 1.0)
        self.assertAlmostEqual(bd["usd_holdings"], 1000.0 * 51.0)
        self.assertAlmostEqual(bd["gold_price"], 10000.0)
        self.assertAlmostEqual(bd["egx_market"], 10000.0 - invested)
        parts = bd["usd_rate"] + bd["usd_holdings"] + bd["gold_price"] + bd["egx_invested"] + bd["egx_market"] + bd["other"]
        self.assertAlmostEqual(parts, bd["total_change"])
        self.assertAlmostEqual(bd["total_change_pct"], 10.0)
        # without rates the split is unavailable and everything lands in "other"
        bd2 = change_breakdown({"total_egp": 100.0}, {"total_egp": 110.0})
        self.assertIsNone(bd2["usd_rate"])
        self.assertAlmostEqual(bd2["other"], 10.0)
        self.assertAlmostEqual(real_change_pct(10.0, 10.0, 365), 0.0)
        self.assertIsNone(real_change_pct(10.0, 0.0, 365))

    def test_market_helpers(self):
        from tracker.market import parse_realtime_price, us_stock_code

        self.assertEqual(parse_realtime_price('{"close": 55.7, "previousClose": 55.1}'), 55.7)
        self.assertEqual(parse_realtime_price('{"close": "NA", "previousClose": 55.1}'), 55.1)
        self.assertIsNone(parse_realtime_price("not json"))
        self.assertIsNone(parse_realtime_price("[1, 2]"))
        self.assertEqual(us_stock_code("pcor"), "PCOR.US")
        self.assertEqual(us_stock_code("VOD.LSE"), "VOD.LSE")


class InsightsTests(unittest.TestCase):
    def test_sector_aggregation_and_funds(self):
        from tracker import insights as ins

        rows = ins.aggregate_sectors(
            [("TMGH", 600.0, 700.0), ("PHDC", 200.0, 300.0), ("COMI", 100.0, 100.0), ("NBE05", 100.0, 100.0)],
            funds={"NBE05"},
        )
        by = {r["sector"]: r for r in rows}
        self.assertEqual(rows[0]["sector"], "Real Estate")
        self.assertAlmostEqual(by["Real Estate"]["w"], 80.0)
        self.assertAlmostEqual(by["Real Estate"]["ret"], (800 / 1000 - 1) * 100)
        self.assertIn("Funds", by)
        self.assertAlmostEqual(sum(r["w"] for r in rows), 100.0)

    def test_override_wins_and_unknown_is_other(self):
        from tracker import companies as c
        from tracker import insights as ins

        self.assertEqual(c.sector_of("ZZZZ"), "Other")
        self.assertEqual(c.sector_of("ZZZZ", {"ZZZZ": "Mining"}), "Mining")
        self.assertEqual(ins.sector_for("TMGH", overrides={"TMGH": "Holding"}), "Holding")
        self.assertIn("Mining", c.known_sectors({"ZZZZ": "Mining"}))

    def test_sector_share_after_buy(self):
        from tracker import insights as ins

        values = {"TMGH": 400.0, "PHDC": 100.0, "COMI": 500.0}
        r = ins.sector_share_after_buy(values, "TMGH", 400.0, 600.0)
        self.assertEqual(r["sector"], "Real Estate")
        self.assertEqual(r["mates"], ["PHDC"])
        self.assertAlmostEqual(r["before"], 50.0)            # 500 / 1000
        self.assertAlmostEqual(r["after"], 700 / 1200 * 100)  # buy grows the wallet too

    def test_concentration_alerts(self):
        from tracker import insights as ins

        stocks = [{"symbol": "TMGH", "w": 25.0}, {"symbol": "NBE05", "w": 30.0}]
        sectors = [{"sector": "Real Estate", "w": 45.0, "symbols": ["TMGH", "PHDC"]},
                   {"sector": "Funds", "w": 30.0, "symbols": ["NBE05"]}]
        msgs = ins.concentration_alerts(stocks, sectors, 20.0, 40.0, funds={"NBE05"})
        self.assertEqual(len(msgs), 2)  # TMGH over stock limit, Real Estate over sector limit; funds exempt
        self.assertEqual(ins.concentration_alerts(stocks, sectors, 50.0, 50.0, funds={"NBE05"}), [])

    def test_drawdown_stats(self):
        from tracker import insights as ins

        pts = [("2026-06-01", 100.0), ("2026-07-01", 120.0), ("2026-10-01", 90.0)]
        d = ins.drawdown_stats(pts)
        self.assertEqual(d["high"], 120.0)
        self.assertEqual(d["high_date"], "2026-07-01")
        self.assertAlmostEqual(d["from_high_pct"], -25.0)
        self.assertEqual(d["days_since_high"], 92)
        up = ins.drawdown_stats(pts, current=130.0)  # live price above any saved close
        self.assertEqual(up["from_high_pct"], 0.0)
        self.assertEqual(up["days_since_high"], 0)
        self.assertIsNone(ins.drawdown_stats([]))

    def test_dca_plan_status(self):
        from datetime import date as D
        from tracker import insights as ins

        plan = {"amount": 1000.0, "frequency": "Weekly", "start": "2026-10-01"}
        today = D(2026, 10, 29)
        self.assertEqual(ins.dca_plan_status(plan, "2026-10-26", today)["status"], "on track")
        self.assertEqual(ins.dca_plan_status(plan, "2026-10-22", today)["status"], "due")      # due 10-29
        self.assertEqual(ins.dca_plan_status(plan, "2026-10-10", today)["status"], "overdue")  # due 10-17
        self.assertEqual(ins.dca_plan_status(plan, None, today)["status"], "overdue")          # due 10-08
        future = {"amount": 1.0, "frequency": "Monthly", "start": "2026-11-15"}
        self.assertEqual(ins.dca_plan_status(future, None, today)["status"], "not started")

    def test_json_roundtrip_and_garbage(self):
        from tracker import insights as ins

        plans = {"TMGH": {"amount": 5000.0, "frequency": "Monthly", "start": "2026-10-01"}}
        self.assertEqual(ins.load_dca_plans(ins.dump_dca_plans(plans)), plans)
        self.assertEqual(ins.load_dca_plans("not json"), {})
        self.assertEqual(ins.load_dca_plans(None), {})
        self.assertEqual(ins.load_sector_overrides(ins.dump_sector_overrides({"A": "X", "B": " "})), {"A": "X"})

    def test_rebased_comparison_and_index_return(self):
        from tracker import insights as ins

        nw = [{"date": "2026-10-10", "total_egp": 1000.0}, {"date": "2026-10-20", "total_egp": 1100.0}]
        ix = [("2026-10-09", 50000.0), ("2026-10-20", 45000.0)]
        out = ins.rebased_comparison(nw, ix)
        self.assertEqual(out[0]["Net worth"], 100.0)
        self.assertAlmostEqual(out[1]["Net worth"], 110.0)
        self.assertAlmostEqual(out[1]["EGX30"], 90.0)
        self.assertEqual(ins.rebased_comparison([], ix), [])
        self.assertAlmostEqual(ins.index_return_pct(ix, "2026-10-09"), -10.0)

    def test_rebalance_plan(self):
        from tracker import insights as ins

        values = {"TMGH": 500.0, "PHDC": 100.0, "COMI": 200.0, "ETEL": 100.0, "NBE05": 100.0}
        plan = ins.rebalance_plan(values, funds={"NBE05"}, sector_limit=40.0, stock_limit=30.0)
        self.assertEqual(plan["total"], 1000.0)
        by = {(o["kind"], o["name"]): o for o in plan["overs"]}
        re_ = by[("sector", "Real Estate")]            # 600 of 1000, limit 400
        self.assertAlmostEqual(re_["excess"], 200.0)
        self.assertAlmostEqual(sum(t["amount"] for t in re_["trims"]), 200.0)
        self.assertEqual(re_["trims"][0]["symbol"], "TMGH")  # largest holding trimmed first
        self.assertAlmostEqual(re_["dilute_cash"], 500.0)    # 600 / 0.4 - 1000
        self.assertAlmostEqual(by[("stock", "TMGH")]["excess"], 200.0)  # 500 vs 300 cap
        self.assertNotIn(("sector", "Funds"), by)             # funds never flagged
        self.assertNotIn("Funds", plan["sector_room"])
        self.assertAlmostEqual(plan["sector_room"]["Banks"], 200.0)     # cap 400 - 200
        self.assertEqual(plan["sector_room"]["Real Estate"], 0.0)
        self.assertEqual(ins.rebalance_plan({}, ())["overs"], [])

    def test_allocate_cash(self):
        from tracker import insights as ins

        out = ins.allocate_cash({"A": 100.0, "B": 300.0, "C": 0.0}, 200.0)
        self.assertAlmostEqual(out["A"], 50.0)
        self.assertAlmostEqual(out["B"], 150.0)
        self.assertNotIn("C", out)
        capped = ins.allocate_cash({"A": 10.0, "B": 300.0}, 400.0)  # more cash than total room
        self.assertAlmostEqual(capped["A"], 10.0)            # never exceeds headroom
        self.assertAlmostEqual(capped["B"], 300.0)
        self.assertAlmostEqual(sum(ins.allocate_cash({"A": 50.0}, 500.0).values()), 50.0)

    def test_alpha_series(self):
        from tracker import insights as ins

        curve = [
            {"date": "2026-01-01", "value": 1000.0, "contributed": 1000.0},
            {"date": "2026-01-02", "value": 1100.0, "contributed": 1000.0},  # +10%
            {"date": "2026-01-03", "value": 2100.0, "contributed": 2000.0},  # deposit 1000, +0% on the day
        ]
        ix = [("2026-01-01", 100.0), ("2026-01-02", 105.0), ("2026-01-03", 105.0)]
        rows = ins.alpha_series(curve, ix, "2026-01-01")
        self.assertAlmostEqual(rows[-1]["portfolio_pct"], 10.0)  # the deposit isn't a gain
        self.assertAlmostEqual(rows[-1]["index_pct"], 5.0)
        self.assertAlmostEqual(rows[-1]["alpha_pp"], 5.0)
        self.assertEqual(ins.alpha_series(curve, [], "2026-01-01"), [])
        self.assertEqual(ins.alpha_series(curve, ix, "2026-01-03"), [])  # needs 2+ days

    def test_modified_dietz_ytd(self):
        from tracker import insights as ins

        # No flows: value 1000 -> 1100 over the year = +10%.
        curve = [
            {"date": "2025-12-31", "value": 1000.0, "contributed": 1000.0},
            {"date": "2026-01-01", "value": 1000.0, "contributed": 1000.0},
            {"date": "2026-01-11", "value": 1100.0, "contributed": 1000.0},
        ]
        r = ins.modified_dietz_ytd(curve, "2026-01-01")
        self.assertAlmostEqual(r["profit"], 100.0)
        self.assertAlmostEqual(r["return_pct"], 10.0)
        # A deposit must not count as profit.
        curve2 = [
            {"date": "2025-12-31", "value": 1000.0, "contributed": 1000.0},
            {"date": "2026-01-01", "value": 1000.0, "contributed": 1000.0},
            {"date": "2026-01-11", "value": 1600.0, "contributed": 1500.0},
        ]
        self.assertAlmostEqual(ins.modified_dietz_ytd(curve2, "2026-01-01")["profit"], 100.0)
        self.assertIsNone(ins.modified_dietz_ytd(curve[:1], "2026-01-01"))


if __name__ == "__main__":
    unittest.main()
