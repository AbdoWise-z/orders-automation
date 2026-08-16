# Fakturama automation layer

Order-first UIA automation that drives Fakturama from an extracted order record
through a saved, verified Invoice. This is the `automation/` package plus the
Flask glue in `automation_web.py`.

## Setup (Windows)

```powershell
pip install -r requirements-automation.txt
```

Register the web routes in `app.py` (after `load_order`/`save_order`/`ORDERS_DIR`):

```python
from automation_web import register_automation_routes
register_automation_routes(app, ORDERS_DIR, load_order, save_order)
```

## Run

1. Start Fakturama and open its workspace/database.
2. **Verify grounding first** (does not touch anything):
   ```powershell
   python -m automation.doctor
   ```
   Every check should read `OK`. Because Fakturama's UIA `AutomationId` is just
   the window handle in decimal, it changes on every launch — the doctor proves
   our Name/structure-based grounding holds on *this* launch.
3. From the review page, open the order's Automation page and click **Run**
   (choose how far to run while the flow is being built out), or:
   ```python
   from automation.runner import run_automation
   from app import allowed_file, order_path, save_order, load_order, to_number
   run_automation(load_order("be7e9340c4ea"), stop_after="header")
   ```

## Architecture

`uia.py` is the only module that imports `uiautomation`. Everything else is
semantic (`widgets.py`) or flow intent (`flows/*`). Grounding rules:

* **Never** use `AutomationId` (== HWND, unstable).
* Prefer the stable `Name` on labelled edits/buttons.
* For empty-`Name` fields use structure/geometry: the Date field by value-regex;
  the Addresses/Items icon pairs by top-to-bottom order under their label.
* Edits are set by **paste**, not `ValuePattern.SetValue`, so SWT modify
  listeners fire and the binding commits.

## Verification (flagged)

The doc's verification (§4.5, §5.5) reads the **Documents** list, and item lines
live in the **Items** grid. Both are NatTable canvases that expose **no rows to
UIA**, so those reads require screenshot + OCR. **A direct read of the embedded
H2 database would be substantially more reliable** and is the recommended
upgrade; verification is isolated so it can be swapped without touching flows.
Order-level totals (Total Net / VAT / Total) *are* plain edits and are verified
via UIA today.

## What's implemented vs pending

Implemented and grounded from the current UIA capture:
* Open New Order; set Date (value-regex) + Cust.Ref.; verify Net / With VAT.
* Verify order totals against the source (numbers only; `$` locale ignored).
* Save via the toolbar; open the follow-up Invoice (preserving the Order link).
* Debtor create-branch Main-address fields (Company, First/Last, Street,
  ZIP-City, Country, E-Mail, Telephone).
* Model-side arithmetic: gross unit price, `VAT n%` names, line/order totals.

Pending a live UIA capture of these screens (each `raise ManualReviewRequired`
names exactly what it needs):
* `Select the address` and `Select a product` dialogs — result-row pick.
* New Debtor **Miscellaneous** + **Payment** tabs; **terms of payment** editor.
* **Data > VATs** editor; **New product** editor; Items grid in-cell editing.
* New **Invoice** editor `paid` controls.

## Captures needed next

With Fakturama open on each screen, dump the UIA subtree (same inspector you used
for the first dump) for: a New Debtor editor showing the **Miscellaneous** and
**Payment** tabs; the **Select the address** dialog with results; the **Select a
product** dialog with results; **Data > VATs**; **terms of payment** (new entry);
**New product**; and a follow-up **Invoice** editor. Those unblock every pending
flow above.
