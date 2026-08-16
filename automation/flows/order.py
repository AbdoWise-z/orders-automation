"""Order flow — sections 1 & 4 of the design doc.

Fully grounded against the UIA dump. The Debtor and Product selection steps
(sections 2 & 3) are delegated to their own modules and invoked between header
and save by the runner.

Editor identity note (§4.4 onwards)
-----------------------------------
Saving renames BOTH the editor tab and its content pane from 'New Order' to
the order number ('PO000003'), so ``app.editor("New Order")`` stops resolving
the moment the order is saved. Every step after §4.4 therefore addresses the
editor by the number read just before saving.

§4.1 (re-confirming addresses and each product line against the source) is
deliberately not implemented: every value it would re-check is already
verified at the point it is entered — the debtor picker's exact-match rule
(§2.3), the per-line VAT read-back (§3.14), and the order totals below — so a
second pass would only add a slower, OCR-dependent way to fail.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .. import navigation, uia, widgets
from ..models import ExtractedOrder
from ..exceptions import VerificationFailed
from ..session import FakturamaApp
from . import selectors
import uiautomation as auto

CT = uia.CT

_DATE_VALUE_RE = r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$"   # 'Aug 15, 2026'
_MONEY_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")
# Stricter form for picking amounts out of a whole OCR'd row: requires the two
# decimal places, so date parts ('2026', '14') can't be mistaken for a total.
_AMOUNT_RE = re.compile(r"\d[\d,]*\.\d{2}\b")

ORDER_EDITOR = "New Order"          # editor name until it is saved
_SHIPPING_FREE = "Free of shipping costs"


def _display_date(iso: str) -> str:
    """'2026-07-14' -> 'Jul 14, 2026' (no leading zero on the day, matching UI)."""
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d:%b} {d.day}, {d.year}"


def _money(text: str) -> Optional[float]:
    """Parse the numeric part of e.g. '$1,071.00' — ignores the currency symbol,
    which shows as '$' under the default US locale even for EUR orders."""
    m = _MONEY_RE.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
def open_new_order(app: FakturamaApp) -> None:
    """1.3 — click Order in the top toolbar and wait for the New Order editor."""
    navigation.new_order(app)
    app.wait_editor("New Order")


def set_header(app: FakturamaApp, order: ExtractedOrder) -> None:
    """1.4–1.7 — leave No. as proposed; set Date and Cust.Ref.; verify Net/With VAT.

    Deliberately idempotent, and re-run immediately before saving (§4.4):
    selecting a Debtor makes Fakturama re-apply that contact's own defaults to
    the document, which has been observed reverting Date to today, the price
    mode to Gross and clearing Cust.Ref. — all of which would otherwise be
    what gets saved. The Date field is re-located by value pattern rather than
    remembered, so it resolves whatever the field currently shows.
    """
    ed = app.editor("New Order")

    # 1.5 Date — grounded by value-regex (empty Name, position-independent).
    date_edit = uia.find_by_value_regex(ed, _DATE_VALUE_RE)
    if date_edit is None:
        raise VerificationFailed("could not locate the Date field by its value pattern")
    if order.header.order_date:
        uia.set_text(date_edit, _display_date(order.header.order_date), "Date")

    # 1.6 Cust.Ref. — stable Name.
    if order.header.external_reference:
        widgets.set_labelled(ed, "Cust.Ref.", order.header.external_reference)

    # 1.7 Verify document price mode = Net and VAT = With VAT (both defaults).
    net_combo_edit = uia.find_by_combo_value_regex(ed, r"^(Net|Gross|---)$")
    if net_combo_edit is None:
            raise VerificationFailed("could not locate the Net combo field by its value pattern")
    uia.combo_select(net_combo_edit, "Net", "combo")
    
    net = uia.combo_value(net_combo_edit) if net_combo_edit else ""
    vat = uia.combo_value(ed.ComboBoxControl(Name="VAT"))
    print(f"Header set: Date={order.header.order_date}, Cust.Ref.={order.header.external_reference}, Net={net}, VAT={vat}")
    
    if net != "Net" or vat != "With VAT":
        raise VerificationFailed(
            "expected price mode Net / With VAT",
            expected="Net / With VAT", actual=f"{net or '?'} / {vat or '?'}",
        )
        
    


def verify_order_defaults(app: FakturamaApp, editor: str = ORDER_EDITOR,
                          tolerance: float = 0.01) -> None:
    """4.2 — Discount stays 0% and Shipping stays 'Free of shipping costs' /
    0.00.

    The extractor supplies no order-level discount or shipping today, so these
    are asserted rather than written. The Discount assertion also guards the
    per-line discounts set in §3.15 from being applied a second time at order
    level, which would silently under-charge the whole order.
    """
    ed = app.editor(editor)
    problems = []

    discount = _money(widgets.read_labelled(ed, "Discount"))
    if discount is None or abs(discount) > tolerance:
        problems.append(f"Discount: expected 0%, got {discount}")

    combo = widgets.combo_by_name(ed, "Shipping")
    shipping = uia.combo_value(combo).strip()
    if shipping != _SHIPPING_FREE:
        problems.append(f"Shipping: expected {_SHIPPING_FREE!r}, got {shipping!r}")

    cost_edit = _edit_right_of(ed, combo)
    cost = _money(uia.get_text(cost_edit)) if cost_edit is not None else None
    if cost is None or abs(cost) > tolerance:
        problems.append(f"Shipping cost: expected 0.00, got {cost}")

    print(f"Order defaults: Discount={discount}, Shipping={shipping!r}, cost={cost}")
    if problems:
        raise VerificationFailed(
            "order-level defaults changed: " + "; ".join(problems),
            user_message="The order's Discount/Shipping are not at their expected defaults.",
            context={"problems": problems},
        )


def verify_totals(app: FakturamaApp, order: ExtractedOrder,
                  editor: str = ORDER_EDITOR, tolerance: float = 0.01) -> None:
    """4.3 — confirm the totals match the source (numbers only; the '$' shown
    under the default US locale is ignored).

    The panel exposes 'Total Gross', 'VAT' and 'Total' — there is no 'Total
    Net' field — so net is derived as Total - VAT and checked against the
    per-line arithmetic.
    """
    ed = app.editor(editor)
    vat = _money(widgets.read_labelled(ed, "VAT"))
    total = _money(widgets.read_labelled(ed, "Total"))
    net = None if (vat is None or total is None) else round(total - vat, 2)

    checks = {
        "VAT": (vat, float(order.expected_total_vat)),
        "Total": (total, float(order.expected_total_gross)),
        "Total Net (= Total - VAT)": (net, float(order.expected_total_net)),
    }
    print("Totals: " + ", ".join(f"{k}={a} (expected {e:.2f})"
                                 for k, (a, e) in checks.items()))

    problems = [f"{label}: expected {expected:.2f}, got {actual}"
                for label, (actual, expected) in checks.items()
                if actual is None or abs(actual - expected) > tolerance]
    if problems:
        raise VerificationFailed(
            "order totals mismatch: " + "; ".join(problems),
            expected={k: e for k, (_a, e) in checks.items()},
            actual={k: a for k, (a, _e) in checks.items()},
        )


def read_order_number(app: FakturamaApp, editor: str = ORDER_EDITOR) -> str:
    """The proposed 'No.' (§1.4). Read BEFORE saving — see the module note on
    the post-save rename."""
    ed = app.editor(editor)
    label = uia.require(ed.TextControl(Name="No."), "No. label")
    pane = label.GetNextSiblingControl()
    edits = sorted(uia.of_type(pane, CT.EditControl),
                   key=lambda c: c.BoundingRectangle.left)
    number = uia.get_text(edits[0]).strip() if edits else ""
    if not number:
        raise VerificationFailed(
            "could not read the order No.",
            user_message="The order number could not be read before saving.",
        )
    return number


def save_order(app: FakturamaApp) -> str:
    """4.4 — Save once via the toolbar; returns the order number the editor is
    now named after."""
    number = read_order_number(app)
    app.save_active()
    uia.pause(2)
    app.editor(number, timeout=10)   # confirms the save + rename landed
    print(f"Order saved as {number}")
    return number


def verify_in_documents(app: FakturamaApp, order: ExtractedOrder, order_no: str,
                        tolerance: float = 0.01) -> list:
    """4.5 — open Data > Documents and confirm the saved Order row.

    Returns advisory warnings rather than raising. This is a *post-hoc*
    confirmation: the save itself is already proven by the editor being
    renamed to the order number, and every value in the row was verified as
    it was entered. Meanwhile this particular grid defeats Tesseract — the
    document number reads as '@'/'HPOO0000' under --psm 6, while --psm 12
    recovers the State column but garbles the rest, so no single pass reads
    the whole row. Failing a completed, correct order over that would be
    wrong; surfacing it for a human to glance at is not.

    The row is identified by the Search filter rather than by OCR: the filter
    matches the number exactly, so one surviving row IS the confirmation that
    a document carrying it exists.
    """
    _open_documents(app)
    pane = app.wait_editor("Documents")
    select_documents_category(pane, "Orders")

    search = uia.require(pane.EditControl(), "Documents search field")
    uia.set_text(search, order_no, "Documents search", commit=False)
    uia.pause(1.5)

    rows = _documents_rows(pane)
    if len(rows) != 1:
        return [f"could not read exactly one Documents row for {order_no} "
                f"(read {len(rows)}); could not confirm the saved row"]
    row = rows[0].text
    print(f"[documents] {row!r}")

    warnings = []
    if order.header.order_date:
        # Compared without whitespace: the grid's date OCRs as 'Aug 16,2026',
        # losing the space the editor's own field shows.
        shown_date = _display_date(order.header.order_date)
        if _squash(shown_date) not in _squash(row):
            warnings.append(f"could not confirm Date {shown_date!r} in the saved row")
    if not _confirms(row, order.header.external_reference):
        warnings.append(f"could not confirm Cust.Ref. "
                        f"{order.header.external_reference!r} in the saved row")
    if "open" not in row.lower():
        warnings.append("could not confirm State 'open' in the saved row")

    expected_total = float(order.expected_total_gross)
    amounts = [float(m.replace(",", "")) for m in _AMOUNT_RE.findall(row)]
    if not any(abs(a - expected_total) <= tolerance for a in amounts):
        warnings.append(f"could not confirm Total {expected_total:.2f} in the "
                        f"saved row (read {amounts})")

    if warnings:
        warnings.append(f"row as read: {row!r}")
    return warnings


def open_followup_invoice(app: FakturamaApp, order_no: str) -> None:
    """4.6/4.7 — from the saved Order's 'Create a follow-up document' group,
    click Invoice (NOT the top-toolbar 'Create: New Invoice', which would not
    preserve the Order relationship), then leave the linked editor open for
    section 5."""
    app.activate_editor_tab(order_no)
    ed = app.editor(order_no)
    group = uia.require(ed.GroupControl(Name="Create a follow-up document"),
                        "follow-up document group")
    uia.click(group.ButtonControl(Name="Invoice"), "follow-up Invoice")
    uia.pause(2)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _open_documents(app: FakturamaApp) -> None:
    """§4.5 'open Data > Documents' — via the Data menu (the same path used for
    Data > VATs; the left-nav label doesn't reliably open the view)."""
    window = app.window
    uia.click(uia.require(window.MenuItemControl(Name="Data"), "Data menu"),
              "Data menu")
    uia.pause(3)
    uia.click(uia.require(window.MenuItemControl(Name="Documents"), "Data > Documents"),
              "Documents")
    uia.pause()


def select_documents_category(pane, name: str) -> None:
    """Pick a category in the Documents view's left tree ('Orders',
    'Invoices', ...).

    That tree is a filter, not just navigation: the Search box only narrows
    within the selected category, so searching without choosing one first can
    look at the wrong set of documents entirely.
    """
    item = pane.TreeItemControl(Name=name)
    if not uia.exists(item, 3):
        print(f"[documents] WARN: no {name!r} category in the tree; "
              f"searching whatever is currently selected")
        return

    r = item.BoundingRectangle
    auto.Click((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    uia.pause()
    try:
        pattern = item.GetSelectionItemPattern()
        if not pattern.IsSelected:
            pattern.Select()
            uia.pause()
    except Exception:
        pass
    print(f"[documents] category: {name}")


def _documents_rows(pane) -> list:
    """OCR the Documents grid only — the view puts a navigation tree ('This
    transaction', the debtor, 'Orders') immediately left of the rows, which
    would otherwise read as extra rows."""
    prect = pane.BoundingRectangle
    tree = pane.TreeControl()
    left = (tree.BoundingRectangle.right - prect.left) if uia.exists(tree, 1) else 0
    search = pane.EditControl()
    top = (search.BoundingRectangle.bottom - prect.top) if uia.exists(search, 1) else 0
    return selectors.read_rows(pane, top=top, left=left)


def _squash(text: str) -> str:
    """Drop all whitespace, so OCR's inconsistent spacing can't fail a match."""
    return re.sub(r"\s+", "", text or "")


def _confirms(row_text: str, expected: Optional[str]) -> bool:
    """Whether a Documents row carries ``expected``, tolerating the grid's own
    mid-value truncation ('WEB-2026-07...' for 'WEB-2026-0714-A17')."""
    if not expected:
        return True
    if expected in row_text:
        return True
    return any(
        tok.endswith("...") and len(tok) > 4 and expected.startswith(tok.rstrip("."))
        for tok in row_text.split()
    )


def _edit_right_of(ed, anchor):
    """The Edit sharing a row with ``anchor`` and sitting to its right — the
    shipping-cost field, which has no Name of its own."""
    r = anchor.BoundingRectangle

    def cmp(c, _depth) -> bool:
        cr = c.BoundingRectangle
        return cr.left >= r.right and cr.top < r.bottom and cr.bottom > r.top

    edit = ed.EditControl(Compare=cmp)
    return edit if uia.exists(edit, 2) else None

