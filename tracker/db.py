"""Tiny SQLite store for transactions. The whole database is a single file."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Optional

from .portfolio import Transaction

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "portfolio.db"


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
                quantity REAL    NOT NULL CHECK (quantity > 0),
                price    REAL    NOT NULL CHECK (price >= 0),
                fee      REAL    NOT NULL DEFAULT 0 CHECK (fee >= 0),
                date     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prices (
                symbol        TEXT PRIMARY KEY,
                current_price REAL NOT NULL,
                updated_at    TEXT NOT NULL
            );
            """
            )
            self._conn.commit()

    def add_transaction(self, txn: Transaction) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO transactions (symbol, side, quantity, price, fee, date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (txn.symbol.upper().strip(), txn.side, txn.quantity, txn.price, txn.fee, txn.date),
            )
            self._conn.commit()
            return int(cur.lastrowid)

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
                quantity=r["quantity"],
                price=r["price"],
                fee=r["fee"],
                date=r["date"],
            )
            for r in self.list_transactions()
        ]

    def set_price(self, symbol: str, price: float, updated_at: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO prices (symbol, current_price, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET current_price = excluded.current_price, "
                "updated_at = excluded.updated_at",
                (symbol.upper().strip(), price, updated_at),
            )
            self._conn.commit()

    def prices(self) -> dict[str, float]:
        with self._lock:
            rows = self._conn.execute("SELECT symbol, current_price FROM prices").fetchall()
        return {r["symbol"]: r["current_price"] for r in rows}

    def get_price(self, symbol: str) -> Optional[float]:
        return self.prices().get(symbol.upper().strip())

    def close(self) -> None:
        self._conn.close()
