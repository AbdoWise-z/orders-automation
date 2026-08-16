"""Debtor flow — section 2 of the design doc.

Grounding status:
  * The 'try to select' branch (§2.1–2.3) uses selectors.run_selector and needs
    a live capture of the 'Select the address' dialog to finish the row-pick.
  * The 'create' branch Main-address fields (§2.5–2.8) ARE in the UIA dump and
    are grounded here: Company, First/Last name, Street, ZIP-City, Country,
    E-Mail, Telephone, plus the address-type roles.
  * Miscellaneous (§2.9), Payment (§2.10) and the terms-of-payment create
    sub-flow (§2.10.1+) need captures of those tabs/editors — marked TODO.

The whole flow keeps the Order tab open; we only switch tabs to fill the new
Debtor editor, then return to the Order and re-select (§2.12).
"""
from __future__ import annotations

import uiautomation

from .. import navigation, normalization as norm, uia, widgets
from ..exceptions import ManualReviewRequired
from ..models import Debtor, ExtractedOrder
from ..session import FakturamaApp
from . import selectors

CT = uia.CT

# The payment-code dropdown's accessible Name is Fakturama's *untranslated*
# i18n key — the label renders literally as '!editorPaymentPaymentcode!' in the
# UI, which is a missing-translation bug on their side. Grounded from a live
# capture; if a future Fakturama release fixes the translation this Name will
# change with it.
_PAYMENT_CODE_LABEL = "!editorPaymentPaymentcode!"

def resolve_or_create(app: FakturamaApp, order: ExtractedOrder) -> list:
    """§2.1-2.13. Returns advisory warnings; genuine faults still raise."""
    debtor = order.debtor

    def open_address_picker() -> None:
        # §2.1 upper existing-contact icon under 'Addresses' (NOT the lower green +)
        ed = app.editor("New Order")
        widgets.upper_icon_under(ed, "Addresses").Click()
        uia.pause()

    def row_is_exact(row_text: str) -> bool:
        # §2.3 exact only when Company, First, Name, ZIP, City all appear.
        parts = [debtor.company, debtor.first_name, debtor.last_name,
                 debtor.billing_address.zip, debtor.billing_address.city]
        return all(p and p in row_text for p in parts)

    def pick():
        return selectors.run_selector(
            app,
            open_picker=open_address_picker,
            dialog_title="Select the address",
            search_term=debtor.company or debtor.contact_name or "",
            row_matches_exact=row_is_exact,
        )

    if pick().selected:
        print("Selected a debtor from the picker; confirming addresses populated")
        return _confirm_addresses_populated(app, order)

    # --- create branch -------------------------------------------------
    print("Creating new Debtor")
    _create_debtor(app, order)
    return _reselect_from_order(app, order, pick)


def _create_debtor(app: FakturamaApp, order: ExtractedOrder) -> None:
    """§2.5-2.11. Fills the parts we can ground today; raises ManualReviewRequired
    for the tabs that still need a capture, so nothing is silently skipped."""
    
    debtor = order.debtor
    navigation.new_contact(app)              # §2.5 left 'New' panel
    ed = app.wait_editor("New Debtor")

    # 1. Payment method
    uia.click(ed.TabItemControl(Name="Miscellaneous"), "Miscellaneous tab")
    uia.pause()

    payment = ed.ComboBoxControl(Name="Payment")
    items = uia.combo_values(payment, "Payment get values")
    payment_value = debtor.payment_method
    print(items)
    if not payment_value:
        raise ManualReviewRequired("Create Payment", "Payment method is required")
    if len([x for x in items if x == payment_value]) > 1:
        raise ManualReviewRequired("Create Payment", "Payment methods are ambiguous.")
    if payment_value not in items:
        ed = _add_payment_method(app, payment_value)

    # re-define it.
    uia.click(ed.TabItemControl(Name="Miscellaneous"), "Miscellaneous tab")
    uia.pause()
    payment = ed.ComboBoxControl(Name="Payment")
    uia.combo_select(payment, payment_value)

    # 2. Addresses
    # §2.6 leave Customer ID as proposed; enter Company / First / Last.
    uia.click(ed.TabItemControl(Name="Addresses"), "Addresses tab")
    uia.pause()

    if debtor.company:
        widgets.set_labelled(ed, "Company", debtor.company)
    _set_first_last(ed, debtor)

    # §2.7 Main address fields (all have stable Names in the dump).
    addr = debtor.billing_address
    widgets.set_labelled(ed, "Street", addr.street)
    _set_zip_city(ed, addr.zip, addr.city)
    if addr.country:
        uia.combo_select(widgets.combo_by_name(ed, "Country"), _country_name(addr.country),
                         "Country")
    widgets.set_labelled(ed, "E-Mail", debtor.email)
    widgets.set_labelled(ed, "Telephone", debtor.phone)

    # §2.8 assign Invoice (+ Delivery when identical) address roles — the
    # 'address type' control is a paned edit+button in the dump; its exact
    # interaction needs a capture of the role picker.
    _set_address_type(ed, debtor)
    
    # 3. Miscellaneous (alias, discount, net)
    uia.click(ed.TabItemControl(Name="Miscellaneous"), "Miscellaneous tab")
    uia.pause()

    widgets.set_labelled(ed, "Alias name", debtor.alias)
    widgets.set_labelled(ed, "Discount", "0%")
    uia.combo_select(ed.ComboBoxControl(Name="Net or Gross"), "Net")

    app.window.SendKeys("{Ctrl}s", waitTime=0.2)
    app.window.SendKeys("{Ctrl}W", waitTime=0.2)



def _set_address_type(ed, debtor: Debtor) -> None:
    """
    """
    address_type = "Invoice address"
    if debtor.delivery_same_as_billing:
        address_type += ",Delivery address"
    label = ed.TextControl(Name="address type")
    row = label.GetNextSiblingControl()
    edits = uia.of_type(row, CT.EditControl)
    edits.sort(key=lambda c: c.BoundingRectangle.left)
    uia.set_text(edits[0], address_type, "address type")
        
def _set_first_last(ed, debtor: Debtor) -> None:
    """The First/Last edits have empty Names; they're the two Edits directly
    after the 'First Name Last Name' Static, left-to-right."""
    label = ed.TextControl(Name="First Name Last Name")
    uia.require(label, "First/Last name label")
    row = label.GetNextSiblingControl()
    edits = uia.of_type(row, CT.EditControl)
    edits.sort(key=lambda c: c.BoundingRectangle.left)
    named = [e for e in edits if not (e.Name or "").strip()][:2]
    if len(named) >= 2:
        if debtor.first_name:
            uia.set_text(named[0], debtor.first_name, "First name")
        if debtor.last_name:
            uia.set_text(named[1], debtor.last_name, "Last name")


def _set_zip_city(ed, zip_code, city) -> None:
    """ZIP + City sit in a paned pair after the 'ZIP - City' Static; the two
    Edits are ZIP (left, narrow) then City (right, wide)."""
    label = ed.TextControl(Name="ZIP - City")
    uia.require(label, "ZIP - City label")
    pane = label.GetNextSiblingControl()
    edits = uia.of_type(pane, CT.EditControl)
    # fall back to searching the label's row if the pane grouping differs
    if len(edits) < 2:
        edits = uia.of_type(uia.parent(pane) or ed, CT.EditControl)
    edits = sorted(edits, key=lambda c: c.BoundingRectangle.left)
    if len(edits) >= 2:
        if zip_code:
            uia.set_text(edits[0], zip_code, "ZIP")
        if city:
            uia.set_text(edits[1], city, "City")


def _country_name(country: str) -> str:
    # The Country combo lists full English names ('Germany', 'United States'),
    # so a code or local name has to be mapped before it is typed.
    from ..normalization import country_name
    return country_name(country) or country


def _confirm_addresses_populated(app: FakturamaApp, order: ExtractedOrder) -> list:
    """§2.4 — after selecting, confirm the Invoice address block filled in.

    Reported rather than raised: the order is still workable and everything
    downstream (totals, the saved Documents row) is verified anyway, so an
    empty preview is worth a human's eye but not worth discarding a run over.
    """
    ed = app.editor("New Order")
    inv = ed.TabControl(Name="Invoice address")
    if uia.exists(inv, 3):
        preview = inv.EditControl()
        if uia.exists(preview, 2) and not uia.get_text(preview).strip():
            return ["the Order's Invoice address preview is empty — check the "
                    "debtor is attached before sending this order"]
    return []


def _reselect_from_order(app: FakturamaApp, order: ExtractedOrder, pick) -> list:
    """§2.12–2.13 — return to the still-open Order and select the debtor we
    just created.

    A miss here is reported, not raised: the debtor itself has been created
    and saved, so the useful part of the work stands and the operator only has
    to attach it — far better than aborting the whole run at this point.
    """
    app.activate_editor_tab("New Order")
    uia.pause()

    result = pick()
    if not result.selected:
        return [f"created the debtor but could not re-select it on the order "
                f"(§2.12): searched "
                f"{order.debtor.company or order.debtor.contact_name!r} and saw "
                f"{result.rows_seen} row(s). Attach it manually in Fakturama."]

    print("Re-selected the newly created debtor on the order")
    return _confirm_addresses_populated(app, order)

def _add_payment_method(app: FakturamaApp, payment_method: str) -> uiautomation.Control:
    """§2.10.1-2.10.6 — create a term of payment, then return to the Debtor
    editor so the caller can select it (§2.10.6).

    Every field is written *before* the single Save, so a field whose label we
    can't resolve aborts with nothing persisted rather than leaving a
    half-configured term of payment behind.
    """
    # §2.10.4 — the payment-code dropdown is a fixed mapping, so a method with
    # no mapping is caught here rather than by guessing a dropdown entry.
    code = norm.payment_code_for(payment_method)
    if not code:
        raise ManualReviewRequired(
            "2.10.4 payment code",
            f"No payment-code mapping for {payment_method!r}. Known methods: "
            f"{', '.join(sorted(norm.PAYMENT_CODE_BY_METHOD))}. Create the term "
            f"of payment manually, or add the mapping.",
            context={"payment_method": payment_method},
        )

    window = app.window
    data = uia.require(window.MenuItemControl(Name="Data"), "Data menu")
    uia.click(data, "Data menu")
    uia.pause(3)

    terms = uia.require(window.MenuItemControl(Name="terms of payment"),
                        "Data > terms of payment")
    uia.click(terms, "terms of payment")
    uia.pause()

    uia.click(window.ButtonControl(Name="Create a new term of payment"),
              "Create a new term of payment")
    pane = app.wait_editor("New Term of Payment")

    missing = []

    def _text(label: str, value: str) -> None:
        try:
            widgets.set_labelled(pane, label, value)
        except Exception:
            missing.append(label)

    _text("Name", payment_method)
    _text("Description", payment_method)

    # §2.10.5 — zero the terms. These already default to 0 on a new term, but
    # they are written explicitly so the record doesn't silently depend on
    # Fakturama's defaults. 'Cash discount' is a percentage field ('0%'); the
    # day counts are plain integers.
    _text("Cash discount", "0%")
    _text("Discount Days", "0")
    _text("Net Days", "0")
    # Text 'unpaid' / 'deposit' / 'paid' are deliberately left blank, and
    # 'Set as standard' is never clicked — it would repoint the account's
    # default term of payment at this new one.

    # §2.10.4 — the payment code. Its accessible Name is the untranslated i18n
    # key (see _PAYMENT_CODE_LABEL); it defaults to 'Mutually defined'.
    try:
        uia.combo_select(pane.ComboBoxControl(Name=_PAYMENT_CODE_LABEL), code,
                         "payment code")
    except Exception as e:
        print(f"Error occurred while selecting payment code: {e}")
        missing.append(f"payment code ({_PAYMENT_CODE_LABEL})")

    if missing:
        # Nothing has been saved yet — close without persisting a partial term.
        window.SendKeys("{Ctrl}S", waitTime=0.2)              
        window.SendKeys("{Ctrl}W", waitTime=0.2)
        raise ManualReviewRequired(
            "2.10.5 term of payment fields",
            f"Could not set {', '.join(missing)} on the New Term of Payment "
            f"editor, so it was abandoned unsaved. These labels need a UIA "
            f"capture of that editor to ground.",
            context={"payment_method": payment_method, "unresolved": missing},
        )

    window.SendKeys("{Ctrl}S", waitTime=0.2)              # Save the payment
    window.SendKeys("{Ctrl}W", waitTime=0.2)              # close the payment
    window.SendKeys("{Ctrl}W", waitTime=0.2)              # close the debtor (we need to start a new one)

    # §2.10.6 — back to the Debtor editor for the caller to select the method.
    navigation.new_contact(app)
    return app.wait_editor("New Debtor")

