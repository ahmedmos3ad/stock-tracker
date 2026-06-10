"""Best-effort market price lookup helpers."""

from __future__ import annotations

from typing import Optional

try:
    import yfinance as yf
except ModuleNotFoundError:
    yf = None

YFINANCE_AVAILABLE = yf is not None


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


def fetch_latest_price(symbol: str) -> Optional[float]:
    """Return the latest daily close for a symbol from Yahoo Finance if available.

    For EGX symbols, we try the plain symbol first and then a Cairo Exchange
    style suffix fallback (e.g. ``COMI.CA``).
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

        price = _latest_from_history(ticker, period="5d", interval="1d")
        if price is not None:
            return price

    return None