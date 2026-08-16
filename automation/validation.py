"""Pre-flight validation: catch bad data *before* the automation touches
Fakturama.

Why this runs first
-------------------
Every one of these problems is otherwise discovered mid-run, by which point
Fakturama already holds a half-built order — a debtor created, some products
created, a couple of lines entered — and unwinding that is manual work. A
missing payment method stops the debtor flow at §2.10; a PAID status with no
date stops the invoice flow at §5.3; an item with no SKU can't be matched at
§3.3 and risks creating a duplicate product. All of it is knowable from the
record alone, so it is checked up front.

Issues are either ``block`` (the run should not start) or ``warn`` (worth a
human's eye, but the run can proceed).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from . import normalization as norm


@dataclass(frozen=True)
class Issue:
    level: str      # 'block' | 'warn'
    field: str
    message: str

    def to_dict(self) -> dict:
        return {"level": self.level, "field": self.field, "message": self.message}


def validate(record: dict) -> list[Issue]:
    """Check a (normalised) record. Returns every issue found, worst first."""
    issues: list[Issue] = []
    order = record.get("order") or {}
    debtor = record.get("debtor") or {}
    items = record.get("items") or []

    _check_debtor(debtor, issues)
    _check_order(order, issues)
    _check_items(items, issues)

    issues.sort(key=lambda i: 0 if i.level == "block" else 1)
    return issues


def blocking(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.level == "block"]


# --------------------------------------------------------------------------- #
def _check_debtor(debtor: dict, issues: list[Issue]) -> None:
    company = debtor.get("company")
    contact = debtor.get("contact_name")
    if not company and not contact:
        issues.append(Issue(
            "block", "debtor",
            "No company or contact name, so the address picker has nothing to "
            "search for (§2.2) and would create a blank debtor."))

    method = debtor.get("payment_method")
    if not method:
        issues.append(Issue(
            "block", "debtor.payment_method",
            "A payment method is required — the debtor's Payment field (§2.10) "
            "and the invoice's (§5.2) are both set from it."))
    elif norm.payment_code_for(method) is None:
        # Only an issue if the term has to be *created*, which can't be known
        # without Fakturama — so advisory, and the flow stops cleanly if it
        # does turn out to be needed.
        known = ", ".join(sorted(m.title() for m in norm.PAYMENT_CODE_BY_METHOD))
        issues.append(Issue(
            "warn", "debtor.payment_method",
            f"No payment-code mapping for {method!r} (known: {known}). If this "
            f"term of payment doesn't already exist in Fakturama, creating it "
            f"needs that code (§2.10.4) and the run will stop for review."))

    status = (debtor.get("paid_status") or "").strip().upper()
    if status and status not in {"PAID", "UNPAID"}:
        issues.append(Issue(
            "warn", "debtor.paid_status",
            f"Unrecognised paid status {debtor.get('paid_status')!r}; it will be "
            f"treated as not paid."))
    if status == "PAID" and not debtor.get("payment_date"):
        issues.append(Issue(
            "block", "debtor.payment_date",
            "Paid status is PAID but there is no payment date, which the "
            "invoice's paid block requires (§5.3)."))

    billing = debtor.get("billing_address") or {}
    missing = [k for k in ("zip", "city") if not billing.get(k)]
    if missing:
        issues.append(Issue(
            "warn", "debtor.billing_address",
            f"Billing address is missing {', '.join(missing)}; an existing debtor "
            f"is only matched when Company, First, Name, ZIP and City all appear "
            f"(§2.3), so this will fall through to creating a new one."))
    if not billing.get("street"):
        issues.append(Issue("warn", "debtor.billing_address.street",
                            "No street — a created debtor will have an incomplete address."))


def _check_order(order: dict, issues: list[Issue]) -> None:
    if not order.get("order_date"):
        issues.append(Issue("warn", "order.order_date",
                            "No order date; Fakturama's proposed date (today) will stand."))
    elif norm.to_iso_date(order.get("order_date")) is None:
        issues.append(Issue("block", "order.order_date",
                            f"Order date {order.get('order_date')!r} is not a date "
                            f"we can parse."))
    if not order.get("external_reference"):
        issues.append(Issue("warn", "order.external_reference",
                            "No Cust.Ref.; the saved order can't be cross-checked by "
                            "reference (§4.5)."))


def _check_items(items: list, issues: list[Issue]) -> None:
    if not items:
        issues.append(Issue("block", "items", "The order has no line items."))
        return

    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        where = f"items[{index}]"
        label = item.get("sku") or item.get("description") or f"row {index + 1}"

        sku = item.get("sku")
        if not sku:
            issues.append(Issue(
                "block", f"{where}.sku",
                f"{label}: no SKU. Products are matched on an exact SKU (§3.3); "
                f"without one this would create a duplicate product."))
        elif sku in seen:
            issues.append(Issue(
                "warn", f"{where}.sku",
                f"SKU {sku!r} appears on rows {seen[sku] + 1} and {index + 1}; "
                f"they will become two separate lines."))
        else:
            seen[sku] = index

        if not item.get("description"):
            issues.append(Issue("warn", f"{where}.description",
                                f"{label}: no description, so a created product would "
                                f"have a blank Name (§3.8)."))

        for key in ("quantity", "unit_net_price", "vat_pct"):
            value = item.get(key)
            if value is None:
                issues.append(Issue("block", f"{where}.{key}",
                                    f"{label}: {key.replace('_', ' ')} is missing; the "
                                    f"line total and VAT can't be computed."))
            elif _as_decimal(value) is None:
                issues.append(Issue("block", f"{where}.{key}",
                                    f"{label}: {key} is not a number ({value!r})."))

        _check_rates(item, where, label, issues)
        _check_line_total(item, where, label, issues)


def _check_rates(item: dict, where: str, label: str, issues: list[Issue]) -> None:
    for key in ("vat_pct", "discount_pct"):
        value = _as_decimal(item.get(key))
        if value is None:
            continue
        if value < 0:
            issues.append(Issue("block", f"{where}.{key}",
                                f"{label}: {key} is negative ({value})."))
        elif 0 < value < 1:
            # 0.19 almost certainly means 19%, but acting on that guess would
            # change what the customer is billed — so flag, never convert.
            issues.append(Issue(
                "warn", f"{where}.{key}",
                f"{label}: {key} is {value}, which will be used as {value}% — if a "
                f"fraction was meant, enter {value * 100:g} instead."))
        elif key == "discount_pct" and value > 100:
            issues.append(Issue("block", f"{where}.discount_pct",
                                f"{label}: discount is {value}%, above 100%."))
        elif key == "vat_pct" and value > 100:
            issues.append(Issue("block", f"{where}.vat_pct",
                                f"{label}: VAT is {value}%, above 100%."))


def _check_line_total(item: dict, where: str, label: str, issues: list[Issue]) -> None:
    """Cross-check the extractor's own line total against the arithmetic. A
    mismatch usually means a misread price or quantity."""
    stated = _as_decimal(item.get("line_net_total"))
    if stated is None:
        return
    qty = _as_decimal(item.get("quantity"))
    price = _as_decimal(item.get("unit_net_price"))
    if qty is None or price is None:
        return
    discount = _as_decimal(item.get("discount_pct")) or Decimal(0)
    try:
        computed = norm.line_net_total(qty, price, discount)
    except (InvalidOperation, ArithmeticError):
        return
    if abs(computed - norm.round2(stated)) > Decimal("0.01"):
        issues.append(Issue(
            "warn", f"{where}.line_net_total",
            f"{label}: stated line total {stated} but quantity x price - discount "
            f"comes to {computed}. The order totals will use {computed}."))


def _as_decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
