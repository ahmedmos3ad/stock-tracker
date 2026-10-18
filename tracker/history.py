"""Historical daily closes from EODHD (same API key as the Prices tab).

Uses the end-of-day endpoint: one API call per symbol, returning every daily bar since
``start``. We store the *raw* ``close`` (not ``adjusted_close``) because the ledger holds
raw share counts and bonus issues are modelled explicitly as extra shares.
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import urlopen

# Stored under this key in the daily_closes table. EODHD's code for the EGX30 index.
EGX30_SYMBOL = "EGX30.INDX"


def eodhd_code(symbol: str) -> str:
    """EGX stocks are '<TICKER>.EGX'; index codes (already '.INDX') are used as-is."""
    if symbol.upper().endswith(".INDX"):
        return symbol.upper()
    return f"{symbol.split('.')[0].upper().strip()}.EGX"


def parse_eod(payload: object) -> list[tuple[str, float]]:
    """EODHD EOD JSON -> [(YYYY-MM-DD, close)], ignoring bars without a positive close."""
    if not isinstance(payload, list):
        return []
    points = []
    for bar in payload:
        try:
            close = float(bar["close"])
            day = str(bar["date"])[:10]
        except (KeyError, TypeError, ValueError):
            continue
        if close > 0:
            points.append((day, close))
    return sorted(points)


def fetch_history(
    symbol: str, start_iso: str, api_key: str, timeout: int = 30
) -> tuple[Optional[list[tuple[str, float]]], Optional[str]]:
    """Daily closes for ``symbol`` since ``start_iso``.

    Returns ``(points, None)`` on success or ``(None, reason)`` on failure, so the UI can
    say *why* (bad key, daily quota exhausted, symbol not on the plan, ...).
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return None, "no API key"
    url = (
        f"https://eodhd.com/api/eod/{quote(eodhd_code(symbol))}"
        f"?api_token={quote(api_key)}&fmt=json&period=d&from={quote(start_iso)}"
    )
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        reasons = {401: "invalid API key", 402: "daily limit reached", 403: "not included in your plan",
                   404: "symbol not found", 429: "too many requests / daily limit"}
        return None, reasons.get(e.code, f"HTTP {e.code}")
    except (URLError, TimeoutError):
        return None, "network error"
    except (ValueError, OSError):
        return None, "bad response"
    points = parse_eod(payload)
    return (points, None) if points else (None, "no data returned")
