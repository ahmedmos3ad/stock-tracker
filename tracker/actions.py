"""Corporate actions (bonus shares / splits) expressed as synthetic zero-cost buys.

A bonus issue or a forward split gives you extra shares for free. In the cost-basis
engine that is exactly a BUY with price 0 and no fee: total cost is unchanged, the share
count goes up, so the average cost and break-even drop by the right amount - with no
changes to the portfolio maths itself.

``new_shares`` / ``per_held`` is the number of *extra* shares credited per shares held:

- bonus 1 for every 10 held  -> new_shares=1, per_held=10
- 2-for-1 split              -> new_shares=1, per_held=1  (one extra per share held)

Fractions are rounded down (brokers pay cash for fractional entitlements).
Rights issues are not handled here: record the subscription as a normal buy.
Reverse splits are not supported.
"""

from __future__ import annotations

from typing import Iterable

from .portfolio import BUY, SELL, Transaction


def quantity_held(transactions: Iterable[Transaction], symbol: str, on_or_before: str) -> int:
    """Shares of ``symbol`` held after all transactions dated <= ``on_or_before``."""
    relevant = [t for t in transactions if t.symbol == symbol and t.date <= on_or_before]
    qty = 0
    for t in sorted(relevant, key=lambda x: (x.date, x.side == SELL)):
        if t.side == BUY:
            qty += t.quantity
        else:
            qty -= min(t.quantity, qty)
    return qty


def resolve_actions(transactions: list[Transaction], actions: list[dict]) -> list[dict]:
    """Each action with the number of shares it credits, applied in date order.

    Earlier actions count towards later ones (bonus on bonus compounds).
    Returns dicts: the original action fields plus ``held`` and ``extra``.
    """
    working = list(transactions)
    resolved = []
    for a in sorted(actions, key=lambda x: (x["date"], x.get("id", 0))):
        held = quantity_held(working, a["symbol"], a["date"])
        extra = (held * int(a["new_shares"])) // int(a["per_held"])
        resolved.append({**a, "held": held, "extra": extra})
        if extra > 0:
            working.append(Transaction(a["symbol"], BUY, extra, 0.0, 0.0, a["date"]))
    return resolved


def apply_corporate_actions(transactions: list[Transaction], actions: list[dict]) -> list[Transaction]:
    """The ledger plus one synthetic zero-cost buy per action that credits shares."""
    result = list(transactions)
    for r in resolve_actions(transactions, actions):
        if r["extra"] > 0:
            result.append(Transaction(r["symbol"], BUY, r["extra"], 0.0, 0.0, r["date"]))
    return result
