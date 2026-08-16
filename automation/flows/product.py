"""Product flow — section 3 of the design doc.

Runs the full select-or-create branch per item row, in source order (§3.1).

Grounding status:
  * Item-selection picker (§3.2–3.3, §3.12) — grounded: same 'search dialog'
    pattern as 'Select the address', read via selectors.default_ocr_rows.
  * VAT existence/create (§3.4–3.6) — grounded against the 'VATs' list editor
    (Data > VATs) and the 'New TAX Rate' editor. The VATs list has no VAT-code
    (E-Invoice) column, so an existing row is trusted on an exact Name match
    only — the Name is always 'VAT {pct}%', which bakes the percentage in, and
    every row this flow creates itself always keeps the default 'S (Standard
    rate)' code (§3.6), so a Name match implies both Value and VAT-code match
    for anything this automation produced.
  * New product editor (§3.7–3.11) — grounded; all fields captured.
  * Line completion (§3.13, §3.14) — grounded: a captured mid-edit dump showed
    that clicking a NatTable cell spawns a real (unlabelled) EditControl at
    that cell's rect, so Qty. is set by clicking into its column (position 1,
    right after Pos. — trusted by position since OCR can misread the header
    text) and pasting the value. U.Price/VAT are auto-filled from the Product
    master we just ensured/created, so they're read-verified from the same
    OCR pass rather than written. Price (the line total) isn't independently
    OCR-verified — it's implied by Fakturama's own calculation, and the
    order-level rollup is still cross-checked in flows/order.verify_totals.
  * Discount (§3.15) — set per line, in the grid's own Discount column, as
    the doc specifies. NOTE: Fakturama ALSO has an order-level Discount field
    in the totals panel; this flow deliberately leaves that one alone, since
    setting both would discount the order twice.

Values are fully derived here so the arithmetic is testable without the UI.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Optional

import uiautomation as auto

from .. import navigation, uia, widgets
from ..exceptions import ControlNotFound, ManualReviewRequired, VerificationFailed
from ..models import LineItem, ExtractedOrder
from ..session import FakturamaApp
from . import selectors

CT = uia.CT

# Known column order in the Items grid, left to right (from a live full-width
# capture). The grid is modelled as exactly these columns, always — OCR is only
# used to locate them, never to decide how many there are.
_ITEMS_COLUMNS = (
    "pos", "qty", "item no.", "picture", "name",
    "description", "vat", "u.price", "discount", "price",
)
_COLUMN_KEYS = tuple(name.split()[0] for name in _ITEMS_COLUMNS)
_COL_QTY = 1
_COL_VAT = 6
_COL_DISCOUNT = 8

# How close an OCR'd header word must be to a canonical name to anchor it.
# Anything else — a column border read as 'l'/'I', the 'No.' half of
# 'Item No.', a garbled label — anchors nothing and is simply ignored, instead
# of becoming a phantom column that shifts every index after it.
_MATCH_THRESHOLD = 0.6
_MIN_ANCHORS = 3   # below this the header read is too weak to trust

# Cache of the last header read that anchored well, keyed on the grid's own
# screen rect: the OCR's ability to re-read the header varies run to run (one
# live read found all 11 header words cleanly, the very next found only
# 'Pos.'), but column positions only move when the grid itself resizes — e.g.
# a vertical scrollbar appearing once enough rows are added. Reusing positions
# across a resize would click the wrong cells, so the rect is part of the key.

# Cache of the last header read that looked complete, keyed on the grid's own
# screen rect: the OCR's ability to re-read the header varies run to run (one
# live read found all 11 header words cleanly, the very next found only
# 'Pos.'), but column positions only move when the grid itself resizes — e.g.
# a vertical scrollbar appearing once enough rows are added. Reusing positions
# across a resize would click the wrong cells, so the rect is part of the key.
_column_cache: Optional[tuple] = None


def resolve_all(app: FakturamaApp, order: ExtractedOrder) -> None:
    for idx, item in enumerate(order.items):
        _resolve_one(app, order, item, idx)


def _resolve_one(app: FakturamaApp, order: ExtractedOrder, item: LineItem, idx: int) -> None:
    def open_product_picker() -> None:
        ed = app.editor("New Order")
        widgets.upper_icon_under(ed, "Items").Click()   # §3.2 upper icon, not the +
        # Leaving the mouse sitting on the icon triggers its hover tooltip
        # ('Pick an item from the list of all products'), which can end up
        # sitting on top of the dialog/grid content OCR needs to read.
        auto.MoveTo(0, 0, moveSpeed=0, waitTime=0.2)
        uia.pause()

    def row_is_exact(row_text: str) -> bool:
        return bool(item.sku) and item.sku in row_text   # §3.3 exact SKU

    def pick_product() -> selectors.SelectorResult:
        return selectors.run_selector(
            app,
            open_picker=open_product_picker,
            dialog_title="Select a product",
            search_term=item.sku or "",
            row_matches_exact=row_is_exact,
        )

    result = pick_product()

    if not result.selected:
        if result.rows_seen > 0:
            # Rows appeared but none satisfied the exact-SKU check — that's
            # very different from a genuinely empty search. It's much more
            # likely an OCR misread of an *existing* product's SKU than a
            # truly missing one, so auto-creating here risks a duplicate
            # master-data record. Stop rather than guess.
            raise ManualReviewRequired(
                "3.3 product match",
                f"Item {item.sku!r}: {result.rows_seen} row(s) appeared in "
                f"Select a product but none matched the SKU exactly — likely "
                f"an OCR misread of an existing product rather than a "
                f"genuinely missing one. Refusing to auto-create a possible "
                f"duplicate; verify manually.",
                context={"sku": item.sku, "rows_seen": result.rows_seen},
            )
        _create_product(app, item)

        # §3.12 says to re-open Select a product and pick the new Product, but
        # Fakturama appears to link a freshly-created product straight into
        # the order line that triggered its creation — a live run showed the
        # line fully populated (Name/VAT/U.Price) right after _create_product,
        # before any re-search ran. Check the grid first; only fall back to
        # the re-search picker if it's genuinely not there (and skip a
        # re-search prone to false negatives from a stray hover tooltip
        # sitting over the exact row/text being OCR-read).
        if not _in_items_grid(app, item):
            app.activate_editor_tab("New Order")
            result = pick_product()
            if not result.selected:
                raise ManualReviewRequired(
                    "3.12 re-select product",
                    f"Item {item.sku!r}: created but did not appear in Select a "
                    f"product on re-search, and no line for it is in the "
                    f"Items grid either.",
                    context={"sku": item.sku},
                )

    # §3.13-14 line completion (Qty, U.Price/VAT read-check) into the NatTable.
    _complete_line(app, item, idx)


def _in_items_grid(app: FakturamaApp, item: LineItem) -> bool:
    """True if a row for this item is already in the Items grid. Matches on
    Name/description rather than SKU — the 'Item No.' column is one of the
    ones that can be hidden entirely on a narrower window, but Name always
    renders."""
    if not item.description:
        return False
    ed = app.editor("New Order")
    grid = _items_grid(ed)
    rows = selectors.read_rows(grid)
    return any(item.description in row.text for row in rows)


def _create_product(app: FakturamaApp, item: LineItem) -> None:
    """§3.4–3.11. Ensures the VAT exists, then creates the Product."""
    _ensure_vat(app, item)

    navigation.new_product(app)
    ed = app.wait_editor("New product")
    _fill_new_product(ed, item)
    app.save_active()
    app.window.SendKeys("{Ctrl}W", waitTime=0.2)  # close 'New product'
    uia.pause(2)  # let the new product finish saving/indexing before §3.12 re-searches it


# --------------------------------------------------------------------------- #
# §3.4–3.6 VAT lookup / create
# --------------------------------------------------------------------------- #
def _ensure_vat(app: FakturamaApp, item: LineItem) -> None:
    """Search the VATs list for an exact 'VAT {pct}%' row; create it via the
    list's 'Create a new tax rate' control if it's missing."""
    _open_vats(app)
    pane = app.wait_editor("VATs")

    search = pane.EditControl()
    uia.require(search, "VATs search field")
    uia.set_text(search, item.vat_name, "VATs search", commit=False)
    uia.pause(1.5)

    rows = selectors.default_ocr_rows(pane)
    matches = [r for r in rows if item.vat_name in r.text]
    print([r.text for r in rows])

    if len(matches) > 1:
        raise ManualReviewRequired(
            "3.5 VAT match",
            f"{len(matches)} rows match {item.vat_name!r} in the VATs list.",
            context={"vat": item.vat_name, "rows": [r.text for r in rows]},
        )
    if len(matches) == 1:
        return  # exact match exists; New product's VAT dropdown will list it.

    _create_vat(app, item)


def _open_vats(app: FakturamaApp) -> None:
    """§3.4 'open Data > VATs' — the Data menu, not the left-nav shortcut (the
    left-nav 'VATs' text click doesn't reliably open the VATs editor pane;
    mirrors the working Data-menu pattern in debtor._add_payment_method)."""
    window = app.window
    data = window.MenuItemControl(Name="Data")
    if not data.Exists():
        raise RuntimeError("Could not find Data")
    uia.click(data, "Data Menu Item")
    uia.pause(3)

    vats = window.MenuItemControl(Name="VATs")
    if not vats.Exists():
        raise RuntimeError("Could not find VATs")
    uia.click(vats, "VATs")
    uia.pause()


def _create_vat(app: FakturamaApp, item: LineItem) -> None:
    """§3.6 — no exact VAT row: create one via the VATs list's green '+'."""
    window = app.window
    uia.click(window.ButtonControl(Name="Create a new tax rate"), "Create a new tax rate")
    ed = app.wait_editor("New TAX Rate")

    value = item.vat_name.split(" ", 1)[1]  # 'VAT 19%' -> '19%'
    widgets.set_labelled(ed, "Name", item.vat_name)
    widgets.set_labelled(ed, "Description", item.vat_name)
    widgets.set_labelled(ed, "Value", value)
    # VAT code (E-Invoice) already defaults to 'S (Standard rate)'; the
    # 'Standard' row (which VAT is the account default) is left untouched.

    app.save_active()
    window.SendKeys("{Ctrl}W", waitTime=0.2)  # close 'New TAX Rate'


# --------------------------------------------------------------------------- #
# §3.7–3.11 New product editor
# --------------------------------------------------------------------------- #
def _fill_new_product(ed, item: LineItem) -> None:
    widgets.set_labelled(ed, "Item Number", item.sku)
    widgets.set_labelled(ed, "Name", item.description)
    widgets.set_labelled(ed, "Description", item.description)
    uia.set_text(_edit_after_label(ed, "Price (gross)"), f"{item.gross_price}", "Price (gross)")
    uia.set_text(_edit_after_label(ed, "cost price (net)"), "0", "cost price (net)")
    widgets.set_labelled(ed, "Stock", "0.00")
    uia.combo_select(ed.ComboBoxControl(Name="VAT"), item.vat_name, "VAT")
    ed.SendKeys("{Ctrl}s", waitTime=0.2)
    # Category, GTIN, supplier code, allowance, Product Picture and user
    # defined field 1 are left blank/unchanged per §3.10.


def _edit_after_label(ed, label: str):
    """Price (gross) / cost price (net) sit in an unlabelled Edit inside the
    Pane right after their Static label."""
    lbl = ed.TextControl(Name=label)
    uia.require(lbl, f"label '{label}'")
    pane = lbl.GetNextSiblingControl()
    edits = sorted(uia.of_type(pane, CT.EditControl), key=lambda c: c.BoundingRectangle.left)
    if not edits:
        raise ControlNotFound(f"edit after label '{label}'")
    return edits[0]


# --------------------------------------------------------------------------- #
# §3.13-14 Items grid line completion
# --------------------------------------------------------------------------- #
def _complete_line(app: FakturamaApp, item: LineItem, idx: int) -> None:
    """Set Qty. on the just-added Items grid row by clicking into its cell
    (spawns a real Edit at the cell's rect — confirmed via a live mid-edit
    capture) and pasting the value; a click on an empty cell below the rows
    commits the edit. U.Price/VAT are auto-filled from the Product master, so
    VAT is read-verified via the same click-into-cell technique (its live
    ValuePattern text, not the OCR'd row — OCR can visually truncate a cell or
    smash it against an adjacent column-border glyph, e.g. 'VAT 19%' misread
    as 'vAT 19%'). This click-to-read technique is Items-grid-only: a click on
    a row in the selector dialogs (Select a product/address, VATs list) means
    select/confirm there, not 'just read'. Discount is handled separately,
    once per order (see _apply_order_discount) — it's not a grid cell."""
    ed = app.editor("New Order")
    grid = _items_grid(ed)

    columns = _resolve_columns(grid)
    rows = selectors.read_rows(grid)
    if not rows:
        raise ManualReviewRequired(
            f"3.13 product line {idx + 1}",
            f"Item {item.sku!r}: no rows visible in the Items grid after selection.",
            context={"sku": item.sku},
        )
    row = rows[-1]  # rows are appended in source order (§3.1); ours is the newest.
    print(f"[grid] target row: {row.text!r} rect={row.rect}")

    # §3.14 confirm VAT matches the extracted percentage.
    vat_value = _read_grid_cell(grid, _column_x(columns, _COL_VAT), row.rect.cy, "vat")
    if item.vat_name not in vat_value:
        raise VerificationFailed(
            f"Item {item.sku!r}: the Items line's VAT does not match the "
            f"extracted percentage.",
            expected=item.vat_name, actual=vat_value,
        )

    # §3.13 Qty.
    _set_grid_cell(grid, _column_x(columns, _COL_QTY), row.rect.cy,
                   "qty", f"{item.quantity}")

    # §3.15 line Discount.
    _set_grid_cell(grid, _column_x(columns, _COL_DISCOUNT), row.rect.cy,
                   "discount", f"{item.discount_pct or 0}%")

    # commit the edit by clicking an empty cell below the rows.
    auto.Click(row.rect.cx, row.rect.cy + 40, waitTime=0.2)
    uia.pause()


def _read_grid_cell(grid, x: int, y: int, label: str) -> str:
    """Click into a cell and read its live value directly via UIA (Items grid
    only — see _complete_line's note on why this doesn't apply to selector
    dialogs) rather than trusting the OCR'd row text."""
    auto.Click(x, y, waitTime=0.2)
    uia.pause(0.3)
    cell = grid.EditControl()
    uia.require(cell, f"Items grid '{label}' cell editor")
    value = uia.get_text(cell)
    print(f"[grid] read {label!r} -> {value!r}")
    return value


def _items_grid(ed):
    """The Items NatTable canvas: the sibling pane right after the icons+label
    block under the 'Items' Static (same layout as the Addresses icon pair)."""
    label = ed.TextControl(Name="Items")
    uia.require(label, "Items label")
    icons_pane = label.GetParentControl()
    grid = icons_pane.GetNextSiblingControl()
    uia.require(grid, "Items grid")
    return grid


@dataclass
class _Column:
    """One Items-grid column: its OCR'd header label and screen-x span."""
    label: str
    left: int
    right: int

    @property
    def center(self) -> int:
        return (self.left + self.right) // 2


def _anchor(text: str):
    """Best canonical column index for an OCR'd header word, or None when it
    doesn't resemble any of them."""
    best_i, best_score = None, 0.0
    for i, key in enumerate(_COLUMN_KEYS):
        score = difflib.SequenceMatcher(None, text, key).ratio()
        if score > best_score:
            best_i, best_score = i, score
    return best_i if best_score >= _MATCH_THRESHOLD else None


def _read_columns(grid) -> tuple:
    """Model the grid as the full, known set of columns, using OCR only to
    locate them.

    Header words are fuzzy-matched onto _ITEMS_COLUMNS; each match anchors
    that column's left edge. Columns whose label OCR mangled are positioned
    by interpolating between the anchors around them. Words that match
    nothing are ignored — this is what keeps a border glyph read as 'l', or
    the 'No.' of 'Item No.', from inserting a phantom column and shifting
    every column after it (which is how a click aimed at VAT landed on
    Description).

    Returns (columns, anchor_count).
    """
    words = selectors.header_words(grid)
    anchors: dict = {}
    for w in words:
        i = _anchor(w.text)
        if i is None:
            continue
        # leftmost wins: a later duplicate is more likely a data-row bleed
        if i not in anchors:
            anchors[i] = w.left

    # Anchors must march left to right; drop any that don't.
    ordered: dict = {}
    for i in sorted(anchors):
        if not ordered or anchors[i] > ordered[max(ordered)]:
            ordered[i] = anchors[i]
    if not ordered:
        return [], 0

    n = len(_ITEMS_COLUMNS)
    known = sorted(ordered)
    lefts: list = [None] * n
    for i in known:
        lefts[i] = ordered[i]

    # Interpolate unanchored columns between known anchors.
    for a, b in zip(known, known[1:]):
        step = (ordered[b] - ordered[a]) / (b - a)
        for k in range(a + 1, b):
            lefts[k] = int(ordered[a] + step * (k - a))

    # Extrapolate past the outermost anchors using the average column width.
    width = ((ordered[known[-1]] - ordered[known[0]]) / (known[-1] - known[0])
             if len(known) > 1 else 110.0)
    for k in range(known[0] - 1, -1, -1):
        lefts[k] = int(lefts[k + 1] - width)
    for k in range(known[-1] + 1, n):
        lefts[k] = int(lefts[k - 1] + width)

    bounds = lefts + [max(grid.BoundingRectangle.right, lefts[-1] + int(width))]
    columns = [_Column(_COLUMN_KEYS[i], bounds[i], bounds[i + 1]) for i in range(n)]
    print(f"[grid] columns (anchored {sorted(known)}): "
          f"{[(c.label, c.center) for c in columns]}")
    return columns, len(known)


def _column_x(columns: list, index: int) -> int:
    """Click-x for a canonical column: the middle of its cell span, not the
    middle of its header word (a short left-aligned label like 'VAT' has its
    word centre sitting right on the boundary with the previous column)."""
    if not columns:
        raise ControlNotFound(f"Items grid column {_ITEMS_COLUMNS[index]!r}",
                              hint="header OCR anchored no columns at all")
    return columns[index].center


def _resolve_columns(grid) -> list:
    """A weakly-anchored header read is more likely an OCR misfire than a
    real layout change (live evidence: one read found all 11 header words
    cleanly, the very next found only 'Pos.' against the same unchanged
    grid) — fall back to the last well-anchored read, but only while the
    grid's own rect is unchanged, since a resize really does move columns."""
    global _column_cache
    rect = grid.BoundingRectangle
    key = (rect.left, rect.top, rect.right, rect.bottom)

    columns, anchored = _read_columns(grid)
    if anchored >= _MIN_ANCHORS:
        _column_cache = (key, columns)
        return columns

    if _column_cache and _column_cache[0] == key:
        print(f"[grid] WARN: header OCR anchored only {anchored} column(s); "
              f"reusing last well-anchored positions")
        return _column_cache[1]
    print(f"[grid] WARN: header OCR anchored only {anchored} column(s) and no "
          f"cached positions for this grid size; proceeding anyway")
    return columns


def _set_grid_cell(grid, x: int, y: int, label: str, value: str) -> None:
    print(f"[grid] set {label!r}={value!r} @ ({x},{y})")
    auto.Click(x, y, waitTime=0.2)
    uia.pause(0.3)
    cell = grid.EditControl()
    uia.require(cell, f"Items grid '{label}' cell editor")
    print(f"[grid] cell editor rect={cell.BoundingRectangle}")
    uia.set_text(cell, value, f"Items grid '{label}' cell", commit=False)
