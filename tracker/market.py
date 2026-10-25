"""Live quotes for Net Worth inputs that used to be typed by hand: USD/EGP rate and US stock prices.

Uses the same EODHD real-time endpoint as the EGX sync (one API call per symbol). EGX funds are never
requested here: only the ``USDEGP.FOREX`` pair and US tickers (``<TICKER>.US``) are fetched.
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.parse import quote
from urllib.request import urlopen

USD_EGP_CODE = "USDEGP.FOREX"


def parse_realtime_price(payload: str) -> Optional[float]:
    """Price from an EODHD real-time JSON payload (``close``, falling back to ``previousClose``)."""
    try:
        decoded = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    for key in ("close", "previousClose"):
        try:
            value = float(decoded.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def fetch_realtime_price(code: str, api_key: str, timeout: float = 20.0) -> Optional[float]:
    """Latest price for a full EODHD code (e.g. ``USDEGP.FOREX`` or ``PCOR.US``); ``None`` on any failure."""
    api_key = (api_key or "").strip()
    code = (code or "").strip()
    if not api_key or not code:
        return None
    url = f"https://eodhd.com/api/real-time/{quote(code)}?api_token={quote(api_key)}&fmt=json"
    try:
        with urlopen(url, timeout=timeout) as response:
            return parse_realtime_price(response.read().decode("utf-8"))
    except Exception:
        return None


def us_stock_code(ticker: str) -> str:
    """EODHD code for a US ticker: 'pcor' -> 'PCOR.US' (an explicit exchange suffix is kept)."""
    t = (ticker or "").strip().upper()
    return t if "." in t else f"{t}.US"
