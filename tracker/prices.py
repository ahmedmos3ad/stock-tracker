"""Best-effort market price lookup helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

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

    Appends the standard '.CA' suffix internally for Yahoo Finance tracking.
    """
    if yf is None:
        return None

    # Clean input and enforce Yahoo's Cairo suffix format (.CA)
    raw_symbol = symbol.split(".")[0].upper().strip()
    yahoo_symbol = f"{raw_symbol}.CA"

    try:
        ticker = yf.Ticker(yahoo_symbol)
        snapshot = _latest_close_snapshot(ticker, yahoo_symbol)
        if snapshot is not None:
            return snapshot
    except Exception:
        pass

    return None


def fetch_latest_price(symbol: str) -> Optional[float]:
    """Return today's daily close for a symbol when available."""

    snapshot = fetch_latest_close(symbol)
    if snapshot is None or not snapshot.is_same_day:
        return None
    return snapshot.price


def fetch_latest_eodhd_close(symbol: str, api_key: str) -> Optional[PriceSnapshot]:
    """Return the latest close/live price for an EGX symbol from EODHD real-time feed.

    No suffixes should be provided to this function. It appends '.EGX' automatically.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return None

    # Strip out any legacy extensions if accidentally provided (e.g., "COMI.CA" -> "COMI")
    raw_symbol = symbol.split(".")[0].upper().strip()
    egx_symbol = f"{raw_symbol}.EGX"

    # Switched to the real-time endpoint to capture instant day-of closing numbers
    url = f"https://eodhd.com/api/real-time/{quote(egx_symbol)}?api_token={quote(api_key)}&fmt=json"

    try:
        with urlopen(url, timeout=20) as response:
            payload = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None
    except Exception:
        return None

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError:
        return None

    # Extract price from the single-ticker real-time dictionary layout
    price = None
    if isinstance(decoded, dict):
        close_price = decoded.get("close")
        previous_close_price = decoded.get("previousClose")

    price = _first_positive([close_price, previous_close_price])
   

    # Keep fallbacks intact to support your internal parsing logic
    if price is None:
        price = _first_positive([decoded])
    if price is None and isinstance(decoded, list):
        price = _first_positive(decoded)
    if price is None and isinstance(decoded, dict):
        price = _first_positive(decoded.values())

    if price is None:
        return None

    return PriceSnapshot(
        symbol=egx_symbol,
        price=price,
        source_date=datetime.now(CAIRO_TZ).date(),
        source_name="EODHD Live/Delayed Close",
    )