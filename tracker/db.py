"""Tiny SQLite store for transactions. The whole database is a single file."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .networth import NetWorthItem
from .portfolio import Dividend, Transaction

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "portfolio.db"

_NW_ITEMS_DDL = """
CREATE TABLE {name} (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    category       TEXT    NOT NULL CHECK (category IN ('gold', 'fx', 'cd', 'cash', 'fund', 'us_stock', 'liability')),
    name           TEXT    NOT NULL,
    quantity       REAL    NOT NULL CHECK (quantity >= 0),
    unit_value     REAL    NOT NULL DEFAULT 0 CHECK (unit_value >= 0),
    karat          INTEGER NOT NULL DEFAULT 24 CHECK (karat IN (21, 24)),
    grams          REAL    NOT NULL DEFAULT 0 CHECK (grams >= 0),
    currency       TEXT    NOT NULL DEFAULT 'EGP',
    fx_rate        REAL    NOT NULL DEFAULT 1.0 CHECK (fx_rate >= 0),
    notes          TEXT    NOT NULL DEFAULT '',
    maturity_date  TEXT    NOT NULL DEFAULT '',
    monthly_amount REAL    NOT NULL DEFAULT 0 CHECK (monthly_amount >= 0),
    updated_at     TEXT    NOT NULL DEFAULT ''
)
"""
_NW_ITEM_COLS = (
    "id, category, name, quantity, unit_value, karat, grams, currency, fx_rate, notes, "
    "maturity_date, monthly_amount, updated_at"
)
# Extra snapshot columns added after the first release (all REAL, default 0 = unknown).
_SNAPSHOT_EXTRA_COLS = ("liabilities_egp", "usd_total_usd", "usd_egp_rate", "gold_24k_price")


def now_iso() -> str:
    """Current Cairo time as an ISO timestamp (seconds precision)."""
    return datetime.now(ZoneInfo("Africa/Cairo")).isoformat(timespec="seconds")


def _as_int_quantity(value: float | int) -> int:
    qty = float(value)
    if not qty.is_integer():
        raise ValueError(f"Invalid non-integer quantity in database: {value!r}")
    return int(qty)


class Store:
    """Thin wrapper over a SQLite connection holding the transaction ledger."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        # Streamlit reruns the script on different threads, so the single cached
        # connection must be usable across threads. A lock serializes access
        # since one sqlite3 connection is not safe for concurrent use.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS transactions (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol   TEXT    NOT NULL,
                side     TEXT    NOT NULL CHECK (side IN ('buy', 'sell')),
                quantity INTEGER NOT NULL CHECK (quantity > 0),
                price    REAL    NOT NULL CHECK (price >= 0),
                fee      REAL    NOT NULL DEFAULT 0 CHECK (fee >= 0),
                date     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prices (
                symbol        TEXT PRIMARY KEY,
                current_price REAL NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dividends (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol   TEXT    NOT NULL,
                amount   REAL    NOT NULL CHECK (amount >= 0),
                date     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS corporate_actions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol     TEXT    NOT NULL,
                date       TEXT    NOT NULL,
                kind       TEXT    NOT NULL CHECK (kind IN ('bonus', 'split')),
                new_shares INTEGER NOT NULL CHECK (new_shares > 0),
                per_held   INTEGER NOT NULL CHECK (per_held > 0)
            );

            CREATE TABLE IF NOT EXISTS daily_closes (
                symbol TEXT NOT NULL,
                date   TEXT NOT NULL,
                close  REAL NOT NULL,
                PRIMARY KEY (symbol, date)
            );

            CREATE TABLE IF NOT EXISTS assets (
                symbol  TEXT PRIMARY KEY,
                is_fund INTEGER NOT NULL DEFAULT 0 CHECK (is_fund IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS net_worth_snapshots (
                date          TEXT PRIMARY KEY,
                total_egp     REAL NOT NULL,
                stocks_egp    REAL NOT NULL DEFAULT 0,
                us_stocks_egp REAL NOT NULL DEFAULT 0,
                funds_egp     REAL NOT NULL DEFAULT 0,
                gold_egp      REAL NOT NULL DEFAULT 0,
                fx_egp        REAL NOT NULL DEFAULT 0,
                cd_cash_egp   REAL NOT NULL DEFAULT 0,
                usd_total_egp REAL NOT NULL DEFAULT 0
            );
            """
            )
            has_items = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='net_worth_items'"
            ).fetchone()
            if not has_items:
                self._conn.execute(_NW_ITEMS_DDL.format(name="net_worth_items"))
            nw_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(net_worth_items)")}
            if "currency" not in nw_cols:
                self._conn.execute("ALTER TABLE net_worth_items ADD COLUMN currency TEXT NOT NULL DEFAULT 'EGP'")
            if "fx_rate" not in nw_cols:
                self._conn.execute("ALTER TABLE net_worth_items ADD COLUMN fx_rate REAL NOT NULL DEFAULT 1.0")
            if "maturity_date" not in nw_cols:
                self._conn.execute("ALTER TABLE net_worth_items ADD COLUMN maturity_date TEXT NOT NULL DEFAULT ''")
            if "monthly_amount" not in nw_cols:
                self._conn.execute("ALTER TABLE net_worth_items ADD COLUMN monthly_amount REAL NOT NULL DEFAULT 0")
            if "updated_at" not in nw_cols:
                self._conn.execute("ALTER TABLE net_worth_items ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
                # Existing items start their freshness clock now (their real last-edit time is unknown).
                self._conn.execute("UPDATE net_worth_items SET updated_at = ?", (now_iso(),))
            nw_sql = self._conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='net_worth_items'"
            ).fetchone()
            if nw_sql and "'liability'" not in nw_sql[0]:
                # Rebuild to widen the category CHECK constraint (SQLite cannot ALTER a CHECK).
                self._conn.execute("ALTER TABLE net_worth_items RENAME TO _net_worth_items_old")
                self._conn.execute(_NW_ITEMS_DDL.format(name="net_worth_items"))
                self._conn.execute(
                    f"INSERT INTO net_worth_items ({_NW_ITEM_COLS}) SELECT {_NW_ITEM_COLS} FROM _net_worth_items_old"
                )
                self._conn.execute("DROP TABLE _net_worth_items_old")
            snap_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(net_worth_snapshots)")}
            for col in _SNAPSHOT_EXTRA_COLS:
                if col not in snap_cols:
                    self._conn.execute(f"ALTER TABLE net_worth_snapshots ADD COLUMN {col} REAL NOT NULL DEFAULT 0")
            # An older version of the app kept a differently-shaped `price_history` table.
            # Copy its rows into daily_closes (never modifying or dropping the old table).
            legacy_cols = {r[1] for r in self._conn.execute("PRAGMA table_info(price_history)")}
            if {"symbol", "current_price", "updated_at"} <= legacy_cols:
                self._conn.execute(
                    "INSERT OR REPLACE INTO daily_closes (symbol, date, close) "
                    "SELECT symbol, substr(updated_at, 1, 10), current_price "
                    "FROM price_history WHERE current_price > 0 ORDER BY id"
                )
            self._conn.commit()

    def add_transaction(self, txn: Transaction) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO transactions (symbol, side, quantity, price, fee, date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (txn.symbol.upper().strip(), txn.side, int(txn.quantity), txn.price, txn.fee, txn.date),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_transaction(self, txn_id: int, txn: Transaction) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE transactions SET symbol = ?, side = ?, quantity = ?, price = ?, fee = ?, date = ? "
                "WHERE id = ?",
                (
                    txn.symbol.upper().strip(),
                    txn.side,
                    int(txn.quantity),
                    txn.price,
                    txn.fee,
                    txn.date,
                    txn_id,
                ),
            )
            self._conn.commit()

    def delete_transaction(self, txn_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
            self._conn.commit()

    def list_transactions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transactions ORDER BY date, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def transactions(self) -> list[Transaction]:
        return [
            Transaction(
                symbol=r["symbol"],
                side=r["side"],
                quantity=_as_int_quantity(r["quantity"]),
                price=r["price"],
                fee=r["fee"],
                date=r["date"],
            )
            for r in self.list_transactions()
        ]
    
    def add_dividend(self, symbol: str, amount: float, date: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO dividends (symbol, amount, date) VALUES (?, ?, ?)",
                (symbol.upper().strip(), amount, date),
            )
            self._conn.commit()
            return int(cur.lastrowid)
    
    
    def delete_dividend(self, dividend_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM dividends WHERE id = ?", (dividend_id,))
            self._conn.commit()

    def update_dividend(self, dividend_id: int, symbol: str, amount: float, date: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE dividends SET symbol = ?, amount = ?, date = ? WHERE id = ?",
                (
                    symbol.upper().strip(), 
                    amount, 
                    date, 
                    dividend_id
                ),
            )
            self._conn.commit()

    def list_dividends(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM dividends ORDER BY date, id"
            ).fetchall()
        return [dict(r) for r in rows]
    
    def dividends(self) -> list[Dividend]:
        return [
            Dividend(
                symbol=r["symbol"],
                amount=r["amount"],
                date=r["date"],
            )
            for r in self.list_dividends()
        ]

    def set_price(self, symbol: str, price: float, updated_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO prices (symbol, current_price, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET current_price = excluded.current_price, "
                "updated_at = excluded.updated_at",
                (symbol.upper().strip(), price, updated_at),
            )
            # Keep a daily history point so the performance charts build up over time.
            self._conn.execute(
                "INSERT INTO daily_closes (symbol, date, close) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol, date) DO UPDATE SET close = excluded.close",
                (symbol.upper().strip(), updated_at[:10], price),
            )
            self._conn.commit()

    # --- corporate actions (bonus shares / splits) --------------------------------
    def add_corporate_action(self, symbol: str, date: str, kind: str, new_shares: int, per_held: int) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO corporate_actions (symbol, date, kind, new_shares, per_held) "
                "VALUES (?, ?, ?, ?, ?)",
                (symbol.upper().strip(), date, kind, int(new_shares), int(per_held)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def delete_corporate_action(self, action_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM corporate_actions WHERE id = ?", (action_id,))
            self._conn.commit()

    def list_corporate_actions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM corporate_actions ORDER BY date, id"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- asset type (stock vs fund) -------------------------------------------------
    def set_fund(self, symbol: str, is_fund: bool) -> None:
        """Mark a symbol as a fund (priced manually). False returns it to the default: a stock."""
        sym = symbol.upper().strip()
        with self._lock:
            if is_fund:
                self._conn.execute(
                    "INSERT INTO assets (symbol, is_fund) VALUES (?, 1) "
                    "ON CONFLICT(symbol) DO UPDATE SET is_fund = 1",
                    (sym,),
                )
            else:
                self._conn.execute("DELETE FROM assets WHERE symbol = ?", (sym,))
            self._conn.commit()

    def fund_symbols(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT symbol FROM assets WHERE is_fund = 1 ORDER BY symbol").fetchall()
        return [r["symbol"] for r in rows]

    # --- user settings (persisted preferences) --------------------------------------
    def set_setting(self, key: str, value: object) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
            self._conn.commit()

    def settings(self) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    # --- net worth items (gold, fx, certificates, cash) -----------------------------
    def add_net_worth_item(
        self,
        category: str,
        name: str,
        quantity: float,
        unit_value: float = 0.0,
        karat: int = 24,
        grams: float = 0.0,
        notes: str = "",
        currency: str = "EGP",
        fx_rate: float = 1.0,
        maturity_date: str = "",
        monthly_amount: float = 0.0,
        updated_at: Optional[str] = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO net_worth_items "
                "(category, name, quantity, unit_value, karat, grams, currency, fx_rate, notes, "
                "maturity_date, monthly_amount, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    category.strip().lower(),
                    name.strip(),
                    float(quantity),
                    float(unit_value),
                    int(karat),
                    float(grams),
                    currency.strip().upper() or "EGP",
                    float(fx_rate),
                    notes.strip(),
                    (maturity_date or "").strip()[:10],
                    float(monthly_amount or 0.0),
                    updated_at or now_iso(),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_net_worth_item(
        self,
        item_id: int,
        category: str,
        name: str,
        quantity: float,
        unit_value: float = 0.0,
        karat: int = 24,
        grams: float = 0.0,
        notes: str = "",
        currency: str = "EGP",
        fx_rate: float = 1.0,
        maturity_date: str = "",
        monthly_amount: float = 0.0,
        updated_at: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE net_worth_items SET category = ?, name = ?, quantity = ?, unit_value = ?, "
                "karat = ?, grams = ?, currency = ?, fx_rate = ?, notes = ?, "
                "maturity_date = ?, monthly_amount = ?, updated_at = ? WHERE id = ?",
                (
                    category.strip().lower(),
                    name.strip(),
                    float(quantity),
                    float(unit_value),
                    int(karat),
                    float(grams),
                    currency.strip().upper() or "EGP",
                    float(fx_rate),
                    notes.strip(),
                    (maturity_date or "").strip()[:10],
                    float(monthly_amount or 0.0),
                    updated_at or now_iso(),
                    int(item_id),
                ),
            )
            self._conn.commit()

    def set_net_worth_item_price(self, item_id: int, unit_value: float, updated_at: Optional[str] = None) -> None:
        """Update only the per-unit price of an item (e.g. a US stock's $/share from a market feed)."""
        with self._lock:
            self._conn.execute(
                "UPDATE net_worth_items SET unit_value = ?, updated_at = ? WHERE id = ?",
                (float(unit_value), updated_at or now_iso(), int(item_id)),
            )
            self._conn.commit()

    def delete_net_worth_item(self, item_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM net_worth_items WHERE id = ?", (int(item_id),))
            self._conn.commit()

    def list_net_worth_items(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM net_worth_items ORDER BY category, id"
            ).fetchall()
        return [dict(r) for r in rows]

    def net_worth_items(self) -> list[NetWorthItem]:
        return [
            NetWorthItem(
                id=int(r["id"]),
                category=r["category"],
                name=r["name"],
                quantity=float(r["quantity"]),
                unit_value=float(r["unit_value"]),
                karat=int(r["karat"]),
                grams=float(r["grams"]),
                currency=str(r.get("currency") or "EGP"),
                fx_rate=float(r.get("fx_rate") if r.get("fx_rate") is not None else 1.0),
                notes=r["notes"],
                maturity_date=str(r.get("maturity_date") or ""),
                monthly_amount=float(r.get("monthly_amount") or 0.0),
                updated_at=str(r.get("updated_at") or ""),
            )
            for r in self.list_net_worth_items()
        ]

    # --- net worth historical snapshots --------------------------------------------
    _SNAPSHOT_COLS = (
        "total_egp", "stocks_egp", "us_stocks_egp", "funds_egp", "gold_egp", "fx_egp", "cd_cash_egp",
        "usd_total_egp",
    ) + _SNAPSHOT_EXTRA_COLS

    def upsert_net_worth_snapshot(self, snap: dict) -> None:
        """Insert or overwrite a daily net worth snapshot keyed by YYYY-MM-DD."""
        cols = self._SNAPSHOT_COLS
        sql = (
            f"INSERT INTO net_worth_snapshots (date, {', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in range(len(cols) + 1))}) "
            f"ON CONFLICT(date) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}"
        )
        values = [str(snap["date"])[:10]] + [float(snap.get(c) or 0.0) for c in cols]
        with self._lock:
            self._conn.execute(sql, values)
            self._conn.commit()

    def delete_net_worth_snapshot(self, date_iso: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM net_worth_snapshots WHERE date = ?",
                (str(date_iso)[:10],),
            )
            self._conn.commit()

    def list_net_worth_snapshots(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM net_worth_snapshots ORDER BY date"
            ).fetchall()
        return [dict(r) for r in rows]

    # --- price history -------------------------------------------------------------
    def upsert_price_history(self, symbol: str, points: list[tuple[str, float]]) -> int:
        """Insert/replace (date, close) points for a symbol. Returns how many were written."""
        if not points:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT INTO daily_closes (symbol, date, close) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol, date) DO UPDATE SET close = excluded.close",
                [(symbol.upper().strip() if not symbol.startswith("^") else symbol, d, float(c)) for d, c in points],
            )
            self._conn.commit()
        return len(points)

    def price_history(self) -> dict[str, list[tuple[str, float]]]:
        """symbol -> [(date, close), ...] sorted by date."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT symbol, date, close FROM daily_closes ORDER BY symbol, date"
            ).fetchall()
        out: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            out.setdefault(r["symbol"], []).append((r["date"], r["close"]))
        return out

    def prices(self) -> dict[str, float]:
        with self._lock:
            rows = self._conn.execute("SELECT symbol, current_price FROM prices").fetchall()
        return {r["symbol"]: r["current_price"] for r in rows}

    def price_entries(self) -> dict[str, dict[str, str | float]]:
        with self._lock:
            rows = self._conn.execute("SELECT symbol, current_price, updated_at FROM prices").fetchall()
        return {
            r["symbol"]: {
                "current_price": r["current_price"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        }

    def get_price(self, symbol: str) -> Optional[float]:
        return self.prices().get(symbol.upper().strip())

    def close(self) -> None:
        self._conn.close()
