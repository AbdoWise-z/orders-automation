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

from .. import navigation, uia, widgets
from ..exceptions import ManualReviewRequired
from ..models import Debtor, ExtractedOrder
from ..session import FakturamaApp
from . import selectors

CT = uia.CT

def resolve_or_create(app: FakturamaApp, order: ExtractedOrder) -> None:
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

    result = selectors.run_selector(
        app,
        open_picker=open_address_picker,
        dialog_title="Select the address",
        search_term=debtor.company or debtor.contact_name or "",
        row_matches_exact=row_is_exact,
        ocr_rows=None,   # None -> selectors.default_ocr_rows (screenshot + OCR)
    )

    if result.selected:
        print("Selected a debtor from the picker; confirming addresses populated")
        _confirm_addresses_populated(app, order)
        return

    # --- create branch -------------------------------------------------
    print("Creating new Debtor")
    _create_debtor(app, order)
    _reselect_from_order(app, order)


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
    # The Country combo lists full English names ('Germany', 'United States').
    return country


def _confirm_addresses_populated(app: FakturamaApp, order: ExtractedOrder) -> None:
    """§2.4 — after selecting, confirm the Invoice/Delivery address blocks filled.
    The address preview is an Edit under the 'Invoice address' tab; we check it's
    non-empty. Deep field-by-field matching is a follow-up."""
    ed = app.editor("New Order")
    inv = ed.TabControl(Name="Invoice address")
    if uia.exists(inv, 3):
        preview = inv.EditControl()
        if uia.exists(preview, 2) and not uia.get_text(preview).strip():
            raise ManualReviewRequired(
                "2.4 confirm addresses",
                "Debtor selected but the Invoice address preview is empty.",
            )


def _reselect_from_order(app: FakturamaApp, order: ExtractedOrder) -> None:
    """§2.12–2.13 — return to the still-open Order, reopen the picker, select the
    newly saved Debtor. (Depends on the selector row-pick being grounded.)"""
    app.activate_editor_tab("New Order")
    resolve_or_create.__wrapped__ if False else None  # no recursion; explicit re-pick TODO
    raise ManualReviewRequired(
        "2.12 re-select debtor",
        "Return-to-Order re-selection pending the selector-dialog capture.",
    )

def _add_payment_method(app: FakturamaApp, payment_method: str) -> uiautomation.Control:
    # 1. Click Data
    window = app.window
    data = window.MenuItemControl(Name="Data")
    if not data.Exists():
        raise RuntimeError("Could not find Data")

    uia.click(data, "Data Menu Item")
    # Give the UI time to open the Data menu
    uia.pause(3)

    # 2. Find and click "terms of payment"
    terms = window.MenuItemControl(Name="terms of payment")
    if not terms.Exists():
        raise RuntimeError("Could not find terms of payment")
    uia.click(terms, "terms of payment")
    uia.pause()

    # now add the element
    uia.click(window.ButtonControl(Name="Create a new term of payment"), "Create a new term of payment")

    pane = window.PaneControl(Name="New Term of Payment")
    widgets.set_labelled(pane, "Name", payment_method)
    widgets.set_labelled(pane, "Description", payment_method)

    uia.click(window.ButtonControl(Name="Save the current contents"), "Save the current contents")
    uia.pause()

    window.SendKeys("{Ctrl}s", waitTime=0.2)
    window.SendKeys("{Ctrl}W", waitTime=0.2)
    window.SendKeys("{Ctrl}W", waitTime=0.2)

    # restore the old editor state
    navigation.new_contact(app)  # §2.5 left 'New' panel
    return app.wait_editor("New Debtor")

