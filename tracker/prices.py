"""Best-effort market price lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import yfinance as yf
except ModuleNotFoundError:
    yf = None

YFINANCE_AVAILABLE = yf is not None
CAIRO_TZ = ZoneInfo("Africa/Cairo")


@dataclass(frozen=True)
class PriceSnapshot:
    symbol: str
    price: float
    source_date: date
    source_name: str = "Yahoo Finance daily close"

    @property
    def is_same_day(self) -> bool:
        return self.source_date == datetime.now(CAIRO_TZ).date()


def _first_positive(values: list[object]) -> Optional[float]:
    for value in values:
        try:
            price = float(value)
        except (TypeError, ValueError):
            continue
        if price > 0:
            return price
    return None


def _latest_from_history(ticker: object, period: str, interval: str) -> Optional[float]:
    try:
        history = ticker.history(period=period, interval=interval, auto_adjust=False)
    except Exception:
        return None

    if history is None or history.empty:
        return None

    close_series = history.get("Close")
    if close_series is None:
        return None

    close_series = close_series.dropna()
    if close_series.empty:
        return None

    return _first_positive(close_series.tolist())


def _latest_close_snapshot(ticker: object, candidate_symbol: str) -> Optional[PriceSnapshot]:
    try:
        history = ticker.history(period="10d", interval="1d", auto_adjust=False)
    except Exception:
        return None

    if history is None or history.empty:
        return None

    close_series = history.get("Close")
    if close_series is None:
        return None

    close_series = close_series.dropna()
    if close_series.empty:
        return None

    latest_price = _first_positive([close_series.iloc[-1]])
    if latest_price is None:
        return None

    latest_index = pd.Timestamp(close_series.index[-1])
    if latest_index.tzinfo is None:
        latest_index = latest_index.tz_localize(CAIRO_TZ)
    else:
        latest_index = latest_index.tz_convert(CAIRO_TZ)

    return PriceSnapshot(symbol=candidate_symbol, price=latest_price, source_date=latest_index.date())


def fetch_latest_close(symbol: str) -> Optional[PriceSnapshot]:
    """Return the latest daily close snapshot for a symbol from Yahoo Finance.

    For EGX symbols, we try the plain symbol first and then a Cairo Exchange
    style suffix fallback (e.g. ``COMI.CA``). The caller can inspect
    ``is_same_day`` to decide whether to accept the result.
    """
    if yf is None:
        return None

    candidates = [symbol.upper().strip()]
    if not candidates[0].endswith(".CA"):
        candidates.append(f"{candidates[0]}.CA")

    for candidate in candidates:
        try:
            ticker = yf.Ticker(candidate)
        except Exception:
            continue

        snapshot = _latest_close_snapshot(ticker, candidate)
        if snapshot is not None:
            return snapshot

    return None


def fetch_latest_price(symbol: str) -> Optional[float]:
    """Return today's daily close for a symbol when available."""

    snapshot = fetch_latest_close(symbol)
    if snapshot is None or not snapshot.is_same_day:
        return None
    return snapshot.price