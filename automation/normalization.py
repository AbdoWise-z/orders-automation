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

import re
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

# The Country combo lists full English names, so anything the extractor emits
# as a code or in the local language has to be mapped before it is typed.
COUNTRY_NAMES = {
    "de": "Germany", "deu": "Germany", "deutschland": "Germany", "germany": "Germany",
    "at": "Austria", "aut": "Austria", "österreich": "Austria", "oesterreich": "Austria",
    "ch": "Switzerland", "che": "Switzerland", "schweiz": "Switzerland",
    "fr": "France", "fra": "France", "frankreich": "France",
    "nl": "Netherlands", "nld": "Netherlands", "niederlande": "Netherlands",
    "be": "Belgium", "bel": "Belgium", "it": "Italy", "ita": "Italy",
    "es": "Spain", "esp": "Spain", "pl": "Poland", "pol": "Poland",
    "gb": "United Kingdom", "uk": "United Kingdom", "gbr": "United Kingdom",
    "us": "United States", "usa": "United States",
}


def country_name(value: Optional[str]) -> Optional[str]:
    """Map a country code or local name onto the Country combo's English name.

    Unknown values are passed through untouched rather than guessed at — a
    wrong country is worse than one the operator has to fix.
    """
    if not value:
        return None
    key = str(value).strip().lower().rstrip(".")
    return COUNTRY_NAMES.get(key, str(value).strip())


def to_iso_date(value: Union[str, date, datetime, None]) -> Optional[str]:
    """Canonicalise a date to ISO ``YYYY-MM-DD``, accepting the formats the
    extractor realistically emits. Returns None if it cannot be parsed —
    callers surface that rather than inventing a date."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return _parse_date_string(str(value).strip()).isoformat()
    except ValueError:
        return None


def normalize_paid_status(value: Optional[str]) -> Optional[str]:
    """Canonicalise to 'PAID' / 'UNPAID'. Anything unrecognised is left as-is
    so validation can flag it instead of it silently reading as unpaid."""
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"paid", "yes", "y", "true", "bezahlt", "settled", "complete"}:
        return "PAID"
    if text in {"unpaid", "no", "n", "false", "offen", "open", "outstanding", "due"}:
        return "UNPAID"
    return str(value).strip()


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
    # Order matters for slash dates: '%m/%d/%Y' is tried first to preserve the
    # US reading, and '%d/%m/%Y' catches what that rejects (e.g. '20/07/2026',
    # where month 20 is impossible). A genuinely ambiguous '07/08/2026' is
    # therefore read US-style rather than guessed at.
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y",
                "%Y/%m/%d", "%d-%m-%Y", "%b %d %Y", "%d %b %Y"):
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
    """Map a payment method to Fakturama's payment-code label (step 2.10.4).

    Idempotent: a value that is already a code maps to itself, so this stays
    correct after ``normalize_record`` has canonicalised the record. Returns
    None when there is no known mapping, in which case the flow stops for
    manual review rather than guessing a dropdown entry.
    """
    if not method:
        return None
    key = method.strip().lower()
    if key in PAYMENT_CODE_BY_METHOD:
        return PAYMENT_CODE_BY_METHOD[key]
    for code in PAYMENT_CODE_BY_METHOD.values():
        if code.lower() == key:
            return code
    return None


def normalize_payment_method(value: Optional[str]) -> Optional[str]:
    """Canonicalise the payment method to the payment-code label Fakturama
    uses ('Bank Transfer' -> 'Credit transfer').

    Applied before the automation runs so every downstream use — the term of
    payment's Name, the debtor's Payment dropdown (§2.10.6) and the invoice's
    (§5.2) — works from the same canonical value. Otherwise a term gets
    created under the raw extracted wording and the dropdown accumulates
    un-normalised entries. Unknown methods pass through cleaned but unchanged.
    """
    cleaned = _clean(value)
    if not cleaned:
        return None
    return payment_code_for(cleaned) or cleaned


def _clean(value):
    """Trim a string and treat blank as absent; leave non-strings alone."""
    if isinstance(value, str):
        text = re.sub(r"\s+", " ", value).strip()
        return text or None
    return value


def _number(value):
    """Parse a numeric field, tolerating '1.234,56' / '€1,234.56' / '19%'.
    Returns a float (the record is stored as JSON) or None."""
    if value in (None, ""):
        return None
    parsed = parse_money(value)
    return float(parsed) if parsed is not None else None


def normalize_record(record: dict) -> list[str]:
    """Canonicalise an extracted record in place; returns a description of
    every value that changed.

    Deliberately conservative: it fixes what is unambiguous (whitespace, date
    formats, country codes, paid wording, number formats) and leaves anything
    genuinely ambiguous alone for ``validation`` to flag. Guessing at, say,
    whether ``0.1`` means 10% or 0.1% would risk mis-billing.
    """
    changes: list[str] = []

    def put(container: dict, key: str, new, label: str) -> None:
        old = container.get(key)
        if old != new:
            container[key] = new
            changes.append(f"{label}: {old!r} -> {new!r}")

    order = record.setdefault("order", {})
    for key in ("external_reference", "customer_id"):
        put(order, key, _clean(order.get(key)), f"order.{key}")
    put(order, "order_date", to_iso_date(order.get("order_date")), "order.order_date")
    currency = _clean(order.get("currency"))
    put(order, "currency", currency.upper() if currency else None, "order.currency")

    debtor = record.setdefault("debtor", {})
    for key in ("company", "contact_name", "alias", "email", "phone"):
        put(debtor, key, _clean(debtor.get(key)), f"debtor.{key}")
    put(debtor, "payment_method",
        normalize_payment_method(debtor.get("payment_method")),
        "debtor.payment_method")
    put(debtor, "paid_status", normalize_paid_status(debtor.get("paid_status")),
        "debtor.paid_status")
    put(debtor, "payment_date", to_iso_date(debtor.get("payment_date")),
        "debtor.payment_date")
    email = debtor.get("email")
    if email:
        put(debtor, "email", email.lower(), "debtor.email")

    for which in ("billing_address", "delivery_address"):
        address = debtor.setdefault(which, {})
        for key in ("street", "zip", "city"):
            put(address, key, _clean(address.get(key)), f"debtor.{which}.{key}")
        put(address, "country", country_name(address.get("country")),
            f"debtor.{which}.country")

    for index, item in enumerate(record.get("items") or []):
        for key in ("sku", "description", "unit"):
            put(item, key, _clean(item.get(key)), f"items[{index}].{key}")
        for key in ("quantity", "unit_net_price", "discount_pct", "vat_pct",
                    "line_net_total"):
            put(item, key, _number(item.get(key)), f"items[{index}].{key}")

    return changes


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
