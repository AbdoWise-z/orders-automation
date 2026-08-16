"""Invoice flow — section 5 of the design doc.

The follow-up Invoice editor is opened by flows/order.open_followup_invoice
(so the Order relationship is preserved). This module then:
  * confirms copied header/lines/totals (§5.1),
  * sets/confirms the payment method (§5.2),
  * applies paid status + payment date + Value when PAID (§5.3),
  * saves (§5.4) and verifies (§5.5).

Grounding status: the New Invoice editor and its 'paid' controls aren't in the
current dump. The payment-status logic (what to set, when) is implemented; field
grounding is pending a capture of the Invoice editor.
"""
from __future__ import annotations

from .. import uia, widgets
from ..exceptions import ManualReviewRequired
from ..models import ExtractedOrder
from ..session import FakturamaApp


def complete_and_verify(app: FakturamaApp, order: ExtractedOrder) -> None:
    ed = app.editor("New Invoice")

    # §5.2 payment method must equal the extracted one.
    method = order.debtor.payment_method

    # §5.3 paid handling — decided here, applied once controls are grounded.
    is_paid = (order.debtor.paid_status or "").upper() == "PAID"
    plan = {
        "payment_method": method,
        "set_paid": is_paid,
        "payment_date": order.debtor.payment_date if is_paid else None,
        "value": order.expected_total_net() if is_paid else None,
    }

    raise ManualReviewRequired(
        "5.2/5.3 invoice payment",
        "Reached the linked Invoice. Payment-status plan is ready; the 'paid' "
        "controls and payment-method field need an Invoice-editor capture to "
        "ground before we set and save them.",
        context=plan,
    )
