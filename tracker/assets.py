"""Stock-vs-fund rules, kept free of Streamlit so they can be unit-tested.

Stocks get their prices from EODHD. Funds (e.g. Beltone-managed, unlisted) have no feed, so their
prices (NAV) are entered by hand and need a staleness warning instead.
"""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional

STALE_NAV_DAYS = 7


def stock_symbols(symbols: Iterable[str], funds: Iterable[str]) -> list[str]:
    """Symbols that should be synced from EODHD: everything that is not a fund (order kept)."""
    fund_set = set(funds)
    return [s for s in symbols if s not in fund_set]


def fund_list(symbols: Iterable[str], funds: Iterable[str]) -> list[str]:
    """The funds among ``symbols`` (order kept)."""
    fund_set = set(funds)
    return [s for s in symbols if s in fund_set]


def stale_navs(
    held_funds: Iterable[str],
    price_entries: Mapping[str, Mapping[str, object]],
    today: date,
    max_age_days: int = STALE_NAV_DAYS,
) -> list[tuple[str, Optional[int]]]:
    """Held funds whose manual price is missing or older than ``max_age_days``.

    Returns ``[(symbol, age_in_days_or_None)]``; ``None`` means no price has ever been saved.
    """
    out: list[tuple[str, Optional[int]]] = []
    for sym in held_funds:
        entry = price_entries.get(sym)
        if not entry or not entry.get("updated_at"):
            out.append((sym, None))
            continue
        try:
            saved_on = date.fromisoformat(str(entry["updated_at"])[:10])
        except ValueError:
            out.append((sym, None))
            continue
        age = (today - saved_on).days
        if age > max_age_days:
            out.append((sym, age))
    return out
