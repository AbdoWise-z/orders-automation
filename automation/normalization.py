"""Pure, testable conversions between the extracted JSON and what Fakturama's
UI expects. No UI Automation here on purpose so this is unit-testable off-box.

Locale note
-----------
The captured install renders US-English: dates as ``Aug 15, 2026`` and currency
as ``$``. The order data is EUR/Germany. We therefore:
* write the Date field as ``Jul 14, 2026`` (NOT ISO ``2026-07-14``);
* compare *numbers* on verification, never currency symbols.
If the target install is reconfigured to another locale, ``to_fakturama_date``
and ``parse_money`` are the two functions to revisit.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional, Union

Number = Union[int, float, str, Decimal]

# Explicit, locale-independent month abbreviations matching Fakturama's display.
_MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Payment method -> Fakturama "payment code (E-Invoice)" dropdown label (step 2.10.4).
PAYMENT_CODE_BY_METHOD = {
    "bank transfer": "Credit transfer",
    "credit card": "Credit card",
    "sepa direct debit": "SEPA direct debit",
}


def round2(value: Number) -> Decimal:
    """Round to 2 dp, half-up (accounting convention)."""
    return _dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _dec(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        raise InvalidOperation("cannot convert None to Decimal")
    return Decimal(str(value))


def to_fakturama_date(value: Union[str, date, datetime, None]) -> Optional[str]:
    """Return e.g. ``'Jul 14, 2026'`` from an ISO string or date/datetime.

    Accepts ISO ``YYYY-MM-DD`` (what the extractor emits) and, defensively, an
    already-formatted ``MMM d, yyyy`` string (returned unchanged after a parse
    round-trip). Returns None for empty input.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        d = value.date()
    elif isinstance(value, date):
        d = value
    else:
        d = _parse_date_string(str(value).strip())
    return f"{_MONTHS[d.month - 1]} {d.day}, {d.year}"


def _parse_date_string(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%d.%m.%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised date format: {s!r}")


def parse_money(text: Union[str, Number, None]) -> Optional[Decimal]:
    """Parse a money string possibly carrying currency symbols/separators.

    Handles US ``$1,234.56`` and EU ``1.234,56``. Returns None for blank input.
    Used to compare Fakturama's displayed totals against expected numbers, so it
    deliberately ignores the currency symbol.
    """
    if text in (None, ""):
        return None
    if isinstance(text, (int, float, Decimal)):
        return _dec(text)

    s = str(text).strip()
    cleaned = "".join(ch for ch in s if ch.isdigit() or ch in ",.-")
    if not cleaned or cleaned in ("-", ".", ","):
        return None

    has_comma, has_dot = "," in cleaned, "." in cleaned
    if has_comma and has_dot:
        # The right-most separator is the decimal point.
        dec_sep = "," if cleaned.rfind(",") > cleaned.rfind(".") else "."
        thou_sep = "." if dec_sep == "," else ","
        cleaned = cleaned.replace(thou_sep, "").replace(dec_sep, ".")
    elif has_comma:
        # Lone comma: decimal if it looks like ...,dd  else thousands.
        frac = cleaned.split(",")[-1]
        cleaned = cleaned.replace(",", "." if len(frac) in (1, 2) else "")
    # lone dot -> already fine
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def gross_from_net(unit_net: Number, vat_pct: Number) -> Decimal:
    """Product master price (step 3.9): net * (1 + vat/100), 2 dp. The
    per-line transaction discount is NOT applied here."""
    return round2(_dec(unit_net) * (Decimal(1) + _dec(vat_pct) / Decimal(100)))


def line_net_total(qty: Number, unit_net: Number, discount_pct: Number = 0) -> Decimal:
    """Expected line Price (step 3.16): qty * unit_net * (1 - discount/100)."""
    factor = Decimal(1) - _dec(discount_pct or 0) / Decimal(100)
    return round2(_dec(qty) * _dec(unit_net) * factor)


def vat_name(vat_pct: Number) -> str:
    """Fakturama VAT record name (step 3.6): ``VAT 19%`` (integer if whole)."""
    d = _dec(vat_pct)
    pct = int(d) if d == d.to_integral_value() else d
    return f"VAT {pct}%"


def payment_code_for(method: Optional[str]) -> Optional[str]:
    """Map an extracted payment method to Fakturama's payment-code label
    (step 2.10.4). Returns None if we don't have a known mapping, in which case
    the flow should stop for manual review rather than guess."""
    if not method:
        return None
    return PAYMENT_CODE_BY_METHOD.get(method.strip().lower())


def split_name(full: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Heuristic first/last split of a single contact name (e.g. 'Marta Klein').

    NOTE: naive last-space split. Fine for 'First Last'; will mis-handle
    multi-part surnames. The flow surfaces the split in its log so a human can
    catch a bad guess.
    """
    if not full:
        return None, None
    parts = full.strip().split()
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])
