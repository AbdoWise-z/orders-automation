"""Invoice flow — section 5 of the design doc.

The linked Invoice editor is opened by flows/order.open_followup_invoice (so
the Order relationship is preserved). This module then confirms what was
copied (§5.1), sets the payment method (§5.2) and paid state (§5.3), saves
(§5.4) and verifies (§5.5-5.6).

Grounding notes
---------------
* Saving renames the editor exactly like an Order: '*New Invoice' becomes the
  invoice number ('INV000001'), pane included, so the number is read before
  saving and used to address the editor afterwards.
* The payment row is a checkbox named 'paid' plus an **unnamed** combo and two
  unnamed fields on the same row, and its contents *change with the paid
  state*: unticked it shows 'Due Days' + 'Pay Until'; ticked it shows the
  payment date + 'Value'. They are therefore resolved by position within the
  row's own pane, and only after the box has been ticked.
* The totals panel is labelled 'Total Net' here, where a Gross-mode Order
  labels the same field 'Total Gross' — so only 'VAT' and 'Total', which are
  named consistently in both, are used for verification.
"""
from __future__ import annotations

from typing import Optional

import uiautomation as auto

from .. import grounding as g
from .. import normalization as norm
from .. import uia, widgets
from ..exceptions import ManualReviewRequired, ValueNotApplied, VerificationFailed
from ..models import ExtractedOrder
from ..session import FakturamaApp
from . import order as order_flow

INVOICE_EDITOR = "New Invoice"       # editor name until it is saved


def complete_and_verify(app: FakturamaApp, order: ExtractedOrder,
                        order_no: Optional[str] = None) -> list:
    """§5.1-5.6. Returns advisory warnings (the Documents reads); anything
    that is genuinely wrong raises.

    ``order_no`` is the source Order's number, needed for §5.5's check that it
    is still listed as open with the same Cust.Ref. and Total.
    """
    ed = app.editor(INVOICE_EDITOR)

    _confirm_copied(ed, order)                       # 5.1
    _set_payment_method(ed, order)                   # 5.2
    _apply_paid_status(ed, order)                    # 5.3

    number = order_flow.read_order_number(app, editor=INVOICE_EDITOR)
    app.save_active()                                # 5.4
    uia.pause(2)
    app.editor(number, timeout=10)                   # confirms save + rename
    print(f"Invoice saved as {number}")

    warnings = _verify_in_documents(app, order, number, order_no)  # 5.5
    warnings += _confirm_persisted(app, order, number)             # 5.6
    return warnings                                  # 5.7 — nothing further


# --------------------------------------------------------------------------- #
# 5.1 confirm what the follow-up copied
# --------------------------------------------------------------------------- #
def _confirm_copied(ed, order: ExtractedOrder, tolerance: float = 0.01) -> None:
    """Confirm the values that prove the Order actually carried over. No./
    Invoice Date/Service date are left exactly as proposed.

    Only the fields that read reliably over UIA are checked — Cust.Ref., VAT
    mode and the totals. Re-reading the copied address block and item lines
    would mean OCR, which on these grids is the least trustworthy thing
    available; the totals already fail if the lines didn't come across.
    """
    problems = []

    ref = order.header.external_reference
    if ref:
        actual_ref = widgets.read_labelled(ed, "Cust.Ref.").strip()
        if actual_ref != ref:
            problems.append(f"Cust.Ref.: expected {ref!r}, got {actual_ref!r}")

    vat_mode = uia.combo_value(ed.ComboBoxControl(Name="VAT")).strip()
    if vat_mode != "With VAT":
        problems.append(f"VAT mode: expected 'With VAT', got {vat_mode!r}")

    vat = order_flow._money(widgets.read_labelled(ed, "VAT"))
    total = order_flow._money(widgets.read_labelled(ed, "Total"))
    for label, actual, expected in (
        ("VAT", vat, float(order.expected_total_vat)),
        ("Total", total, float(order.expected_total_gross)),
    ):
        if actual is None or abs(actual - expected) > tolerance:
            problems.append(f"{label}: expected {expected:.2f}, got {actual}")

    print(f"Invoice copied: Cust.Ref.={ref!r}, VAT mode={vat_mode!r}, "
          f"VAT={vat}, Total={total}")
    if problems:
        raise VerificationFailed(
            "invoice was not copied from the order: " + "; ".join(problems),
            user_message="The invoice does not match the order it was created from.",
            context={"problems": problems},
        )


# --------------------------------------------------------------------------- #
# 5.2 payment method
# --------------------------------------------------------------------------- #
def _set_payment_method(ed, order: ExtractedOrder) -> None:
    """Set/confirm the payment method. The extracted value is used as-is —
    it is normalised upstream, and it is the same string the debtor's own
    Payment combo was set to (§2.10), so the term already exists."""
    method = order.debtor.payment_method
    if not method:
        raise ManualReviewRequired(
            "5.2 invoice payment method",
            "No payment method was extracted, so the invoice's cannot be confirmed.",
        )

    combo = _payment_combo(ed)
    current = _combo_text(combo)
    if current.strip() == method.strip():
        print(f"Invoice payment method already {method!r}")
        return
    try:
        uia.combo_select(combo, method, "invoice payment method")
    except ValueNotApplied as exc:
        raise ManualReviewRequired(
            "5.2 invoice payment method",
            f"The invoice's payment method could not be set to {method!r} "
            f"(reads {_combo_text(combo)!r}); it may not be available on this "
            f"invoice.",
            context={"wanted": method, "actual": _combo_text(combo)},
        ) from exc


# --------------------------------------------------------------------------- #
# 5.3 paid status
# --------------------------------------------------------------------------- #
def _apply_paid_status(ed, order: ExtractedOrder) -> None:
    """PAID -> tick 'paid', set the payment date and Value. Anything else ->
    leave the box clear and invent neither a date nor a value."""
    paid_box = uia.require(ed.CheckBoxControl(Name="paid"), "'paid' checkbox")

    if not order.debtor.is_paid:
        if _is_checked(paid_box):
            uia.click(paid_box, "'paid' checkbox")   # copied state said paid
        print("Invoice left unpaid (source status is not PAID)")
        return

    if not order.debtor.payment_date:
        raise ManualReviewRequired(
            "5.3 invoice paid",
            "Paid status is PAID but no payment date was extracted.",
        )

    if not _is_checked(paid_box):
        uia.click(paid_box, "'paid' checkbox")
        uia.pause()
    if not _is_checked(paid_box):
        raise ValueNotApplied(
            "the 'paid' checkbox did not tick",
            user_message="The invoice could not be marked as paid.",
        )

    # Only now do the date + Value fields exist: ticking 'paid' replaces the
    # 'Due Days'/'Pay Until' pair with them.
    edits = _payment_row_edits(ed)
    if len(edits) < 2:
        raise VerificationFailed(
            f"expected the paid row to expose a date and a Value field, found "
            f"{len(edits)}",
            user_message="The invoice's paid date/value fields could not be found.",
        )
    date_edit, value_edit = edits[0], edits[1]

    uia.set_text(date_edit, norm.to_fakturama_date(order.debtor.payment_date),
                 "invoice payment date")
    # §5.3 'the full Invoice Total' — the same gross total as the order.
    uia.set_text(value_edit, f"{order.expected_total_gross}", "invoice paid Value")
    print(f"Invoice marked paid on {order.debtor.payment_date} "
          f"for {order.expected_total_gross}")


# --------------------------------------------------------------------------- #
# 5.5 / 5.6 verification
# --------------------------------------------------------------------------- #
def _verify_in_documents(app: FakturamaApp, order: ExtractedOrder,
                         invoice_no: str, order_no: Optional[str] = None) -> list:
    """5.5 — confirm the Invoice row, and that the source Order is still
    listed as open with the same Cust.Ref. and Total.

    Advisory for the same reason as §4.5: Tesseract cannot read this grid's
    State column, so a failure here says more about OCR than about the data.
    §5.6 re-reads the same facts over UIA, where they are trustworthy.
    """
    warnings = []
    order_flow._open_documents(app)
    pane = app.wait_editor("Documents")
    order_flow.select_documents_category(pane, "Invoices")

    search = uia.require(pane.EditControl(), "Documents search field")
    uia.set_text(search, invoice_no, "Documents search", commit=False)
    uia.pause(1.5)

    rows = order_flow._documents_rows(pane)
    if len(rows) != 1:
        warnings.append(f"could not read exactly one Documents row for "
                        f"{invoice_no} (read {len(rows)})")
    else:
        row = rows[0].text
        print(f"[documents] invoice: {row!r}")
        expected_total = float(order.expected_total_gross)
        amounts = [float(m.replace(",", ""))
                   for m in order_flow._AMOUNT_RE.findall(row)]
        if not any(abs(a - expected_total) <= 0.01 for a in amounts):
            warnings.append(f"could not confirm invoice Total "
                            f"{expected_total:.2f} in the saved row (read {amounts})")
        if order.debtor.is_paid and "paid" not in row.lower():
            warnings.append("could not confirm invoice State 'paid' in the saved row")
        if warnings:
            warnings.append(f"invoice row as read: {row!r}")

    # ...and the source Order must still be listed, open, with the same
    # Cust.Ref. and Total — which is exactly what §4.5's check does.
    if order_no:
        warnings += order_flow.verify_in_documents(app, order, order_no)
    return warnings


def _confirm_persisted(app: FakturamaApp, order: ExtractedOrder,
                       invoice_no: str) -> list:
    """5.6 — reopen the saved Invoice and confirm the payment method, paid
    state, payment date and Value actually persisted.

    Done every run rather than 'only if needed': these are plain UIA reads, so
    unlike §5.5's OCR they are worth trusting, and they are the only reliable
    confirmation that the paid block survived the save.
    """
    app.activate_editor_tab(invoice_no)
    ed = app.editor(invoice_no)

    problems = []
    method = (order.debtor.payment_method or "").strip()
    actual_method = _combo_text(_payment_combo(ed)).strip()
    if actual_method != method:
        problems.append(f"payment method: expected {method!r}, got {actual_method!r}")

    paid_box = uia.require(ed.CheckBoxControl(Name="paid"), "'paid' checkbox")
    if _is_checked(paid_box) != order.debtor.is_paid:
        problems.append(f"paid: expected {order.debtor.is_paid}, "
                        f"got {_is_checked(paid_box)}")

    if order.debtor.is_paid:
        edits = _payment_row_edits(ed)
        shown_date = uia.get_text(edits[0]).strip() if edits else ""
        expected_date = norm.to_fakturama_date(order.debtor.payment_date) or ""
        if shown_date != expected_date:
            problems.append(f"payment date: expected {expected_date!r}, got {shown_date!r}")

        shown_value = order_flow._money(uia.get_text(edits[1])) if len(edits) > 1 else None
        expected_value = float(order.expected_total_gross)
        if shown_value is None or abs(shown_value - expected_value) > 0.01:
            problems.append(f"Value: expected {expected_value:.2f}, got {shown_value}")

    print(f"Invoice persisted: method={actual_method!r}, paid={_is_checked(paid_box)}")
    if problems:
        raise VerificationFailed(
            "invoice payment details did not persist: " + "; ".join(problems),
            user_message="The invoice's payment details were not saved as expected.",
            context={"problems": problems},
        )
    return []


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _payment_combo(ed):
    """The payment-method combo: unnamed, and the only combo sharing a row
    with the 'paid' checkbox."""
    paid = uia.require(ed.CheckBoxControl(Name="paid"), "'paid' checkbox")
    r = paid.BoundingRectangle

    def cmp(c, _depth) -> bool:
        cr = c.BoundingRectangle
        return cr.left >= r.right and cr.top < r.bottom and cr.bottom > r.top

    combo = ed.ComboBoxControl(Compare=cmp)
    return uia.require(combo, "invoice payment-method combo")


def _payment_row_edits(ed) -> list:
    """The Edits inside the payment row's own pane, left to right. Scoped to
    that pane because the totals' VAT/Total edits sit on the same screen rows
    further right."""
    combo = _payment_combo(ed)
    row_pane = combo.GetParentControl()
    edits = g.find_all(row_pane, control_type="EditControl")
    return sorted(edits, key=lambda c: c.BoundingRectangle.left)


def _combo_text(combo) -> str:
    """Combo's displayed value; falls back to its child label, which is where
    SWT keeps the text for these unnamed combos."""
    value = uia.combo_value(combo)
    if value:
        return value
    child = combo.TextControl()
    return uia.get_text(child) if uia.exists(child, 1) else ""


def _is_checked(box) -> bool:
    try:
        return box.GetTogglePattern().ToggleState == auto.ToggleState.On
    except Exception:
        return False
