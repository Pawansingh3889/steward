"""Reading a bank alert the person already received.

This is emphatically **not** bank aggregation — the plan rules that out, and
nothing here opens a connection to anybody. It reads a notification the bank
already sent to the person's own phone, of the kind they would otherwise retype
into the agent by hand. The money truth stays user-stated; this only saves the
typing.

What comes out is a balance and a spend, in integer minor units. What does not
come out is the account number, the sort code, the card PAN, or the full text.
The regexes below are written to *find* those so they can be dropped, not
because anything downstream wants them: a masked PAN like `****1234` is still a
card reference, and the last four digits plus a merchant plus a date is enough
to identify a transaction to anyone holding the other half.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import FactKind
from .base import PARSED, Candidate

# £12.50 / $1,234.56 / €9 / 12.50 GBP
_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
_AMOUNT = re.compile(
    r"(?P<symbol>[£$€])\s?(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
    r"|(?P<value2>\d{1,3}(?:,\d{3})*(?:\.\d{2})?)\s?(?P<code>GBP|USD|EUR)\b"
)
_BALANCE_NEAR = re.compile(r"\b(balance|bal|available)\b", re.IGNORECASE)
_SPEND_NEAR = re.compile(r"\b(spent|paid|payment|debit|purchase|card ending)\b", re.IGNORECASE)
_MERCHANT = re.compile(r"\bat\s+([A-Z0-9][A-Z0-9 &'\-\.]{1,28})", re.IGNORECASE)
# Where a merchant name stops. Dots are allowed inside one (AMAZON.CO.UK) but a
# dot followed by a space ends it, and so does the " on <date>" clause every
# bank alert appends. Without this the capture ran straight through
# "TESCO STORES. Balance" and stored the next clause as part of the shop's name.
_MERCHANT_END = re.compile(r"\.\s|\s{2,}|\s+on\s+\d", re.IGNORECASE)


def _clean_merchant(raw: str) -> str:
    return _MERCHANT_END.split(raw.strip(), 1)[0].strip(" .,;-")


@dataclass(frozen=True)
class Money:
    minor_units: int
    currency: str

    def format(self) -> str:
        symbol = next((s for s, code in _SYMBOLS.items() if code == self.currency), "")
        return f"{symbol}{self.minor_units / 100:,.2f}".rstrip() or str(self.minor_units)


def _to_minor(value: str) -> int:
    """Integer minor units, like every other amount in this codebase. Floats
    are how money quietly becomes 12.499999999999998."""
    cleaned = value.replace(",", "")
    if "." in cleaned:
        whole, _, fraction = cleaned.partition(".")
        return int(whole) * 100 + int(fraction.ljust(2, "0")[:2])
    return int(cleaned) * 100


def find_amounts(text: str) -> list[tuple[int, Money]]:
    """Every amount and where it appeared, so a caller can tell which clause it
    belonged to."""
    found: list[tuple[int, Money]] = []
    for match in _AMOUNT.finditer(text):
        if match.group("symbol"):
            currency = _SYMBOLS[match.group("symbol")]
            raw = match.group("value")
        else:
            currency = match.group("code")
            raw = match.group("value2")
        if raw:
            found.append((match.start(), Money(_to_minor(raw), currency)))
    return found


def _nearest_label(text: str, position: int, pattern: re.Pattern[str]) -> int:
    """How far the nearest matching label sits from an amount. Bank alerts are
    written as short clauses, so proximity is a better signal than order."""
    best = 10_000
    for match in pattern.finditer(text):
        best = min(best, abs(match.start() - position))
    return best


def extract(raw: str) -> list[Candidate]:
    """Bank alert in, at most one balance and one spend out."""
    amounts = find_amounts(raw)
    if not amounts:
        return []

    balance: Money | None = None
    spend: Money | None = None
    balance_distance = spend_distance = 10_000
    for position, money in amounts:
        to_balance = _nearest_label(raw, position, _BALANCE_NEAR)
        to_spend = _nearest_label(raw, position, _SPEND_NEAR)
        if to_balance < to_spend and to_balance < balance_distance:
            balance, balance_distance = money, to_balance
        elif to_spend <= to_balance and to_spend < spend_distance:
            spend, spend_distance = money, to_spend

    candidates: list[Candidate] = []
    if balance is not None:
        candidates.append(
            Candidate(
                kind=FactKind.CONSTRAINT,
                key="balance",
                value=f"{balance.format()} {balance.currency}",
                source=PARSED,
            )
        )
    if spend is not None:
        merchant = _MERCHANT.search(raw)
        name = _clean_merchant(merchant[1]) if merchant else ""
        where = f" at {name.title()}" if name else ""
        candidates.append(
            Candidate(
                kind=FactKind.SUPPLY,
                key="last_spend",
                value=f"{spend.format()} {spend.currency}{where}",
                source=PARSED,
            )
        )
    return candidates
