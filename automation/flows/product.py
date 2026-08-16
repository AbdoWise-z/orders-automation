"""Product flow — section 3 of the design doc.

Runs the full select-or-create branch per item row, in source order (§3.1).

Grounding status:
  * Item-selection picker (§3.2–3.3) uses selectors.run_selector — needs the
    'Select a product' dialog capture for the row-pick.
  * VAT existence/create (§3.4–3.6) needs the Data > VATs editor capture.
  * New product editor (§3.7–3.11) needs that editor's capture; the VALUES it
    must receive are already computed on the model (LineItem.gross_unit_price,
    vat_name) so only the field grounding remains.
  * Line completion (§3.13–3.16) writes into the Items NatTable — no UIA cells,
    so this uses in-cell editing by navigating the grid (needs capture) or is
    verified via order totals (already implemented in flows/order.verify_totals).

Values are fully derived here so the arithmetic is testable without the UI.
"""
from __future__ import annotations

from .. import uia, widgets
from ..exceptions import ManualReviewRequired
from ..models import LineItem, ExtractedOrder
from ..session import FakturamaApp
from . import selectors


def resolve_all(app: FakturamaApp, order: ExtractedOrder) -> None:
    for idx, item in enumerate(order.items):
        _resolve_one(app, order, item, idx)


def _resolve_one(app: FakturamaApp, order: ExtractedOrder, item: LineItem, idx: int) -> None:
    def open_product_picker() -> None:
        ed = app.editor("New Order")
        widgets.upper_icon_under(ed, "Items").Click()   # §3.2 upper icon, not the +
        uia.pause()

    def row_is_exact(row_text: str) -> bool:
        return bool(item.sku) and item.sku in row_text   # §3.3 exact SKU

    result = selectors.run_selector(
        app,
        open_picker=open_product_picker,
        dialog_title="Select a product",
        search_term=item.sku or "",
        row_matches_exact=row_is_exact,
        ocr_rows=None,
    )

    if not result.selected:
        _create_product(app, item)

    # §3.13–3.16 line completion (Qty / U.Price / Discount) into the NatTable.
    raise ManualReviewRequired(
        f"3.x product line {idx + 1}",
        f"Item {item.sku!r}: selection path reached. Line completion writes into "
        f"the Items NatTable, which needs a grid-editing capture; derived values "
        f"ready (gross {item.gross_unit_price()}, VAT '{item.vat_name}', "
        f"line net {item.computed_line_net()}).",
        context={"sku": item.sku},
    )


def _create_product(app: FakturamaApp, item: LineItem) -> None:
    """§3.4–3.11. Ensures the VAT exists, opens New product, sets Item Number,
    Name/Description, Price (gross), cost price 0, VAT, Stock 0, then Saves.
    Field grounding pending the New product / VATs editor captures."""
    raise ManualReviewRequired(
        "3.7 create product",
        f"Would create product {item.sku!r} with gross price "
        f"{item.gross_unit_price()} and {item.vat_name}. New-product editor "
        f"grounding pending a capture.",
        context={"sku": item.sku, "vat": item.vat_name},
    )
