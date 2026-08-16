"""Order flow — sections 1 & 4 of the design doc.

Fully grounded against the UIA dump. The Debtor and Product selection steps
(sections 2 & 3) are delegated to their own modules and invoked between header
and save by the runner.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .. import navigation, uia, widgets
from ..models import ExtractedOrder
from ..exceptions import VerificationFailed
from ..session import FakturamaApp

_DATE_VALUE_RE = r"^[A-Z][a-z]{2} \d{1,2}, \d{4}$"   # 'Aug 15, 2026'
_MONEY_RE = re.compile(r"[-+]?\d[\d,]*\.?\d*")


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
    """1.4–1.7 — leave No. as proposed; set Date and Cust.Ref.; verify Net/With VAT."""
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
        
    


def verify_totals(app: FakturamaApp, order: ExtractedOrder, tolerance: float = 0.01) -> None:
    """4.3 — confirm Total Net, VAT and Total match the source (numbers only).

    Individual item lines live in a NatTable and can't be read via UIA; the
    order-level totals are plain Edits and are the strongest UIA-only check we
    have. Line-level verification is left to the OCR path (flagged in README).
    """
    ed = app.editor("New Order")
    checks = {
        "Total Net": order.total_net(),
        "VAT": order.total_vat(),
        "Total": order.total_gross(),
    }
    problems = []
    for label, expected in checks.items():
        actual = _money(widgets.read_labelled(ed, label))
        if actual is None or abs(actual - expected) > tolerance:
            problems.append(f"{label}: expected {expected:.2f}, got {actual}")
    if problems:
        raise VerificationFailed("order totals mismatch: " + "; ".join(problems),
                                 expected=checks)


def save_order(app: FakturamaApp) -> None:
    """4.4 — Save once via the toolbar."""
    app.save_active()


def open_followup_invoice(app: FakturamaApp) -> None:
    """4.6 — from the saved Order's 'Create a follow-up document' group, click
    Invoice (NOT the top-toolbar 'Create: New Invoice', so the Order link is
    preserved). Then wait for the linked New Invoice editor."""
    ed = app.editor("New Order")
    group = uia.require(ed.GroupControl(Name="Create a follow-up document"),
                        "follow-up document group")
    uia.click(group.ButtonControl(Name="Invoice"), "follow-up Invoice")
    app.wait_editor("New Invoice")
