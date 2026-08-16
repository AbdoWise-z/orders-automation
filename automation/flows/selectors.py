"""Shared 'search dialog' pattern.

'Select the address' (§2.2), 'Select a product' (§3.3), and the Data > VATs /
terms-of-payment search boxes all follow the same shape: a filter field, a
result list, and OK / Cancel. This module centralises that dance so each flow
just supplies the search term and an OCR/row matcher.

STATUS: interface + partial implementation. The result lists are almost
certainly NatTable (canvas), which exposes no rows to UIA — so selecting the
matching row uses geometry + OCR confirmation. The exact grounding needs a live
capture of one selector dialog (see README 'captures needed'). Everything that
CAN be grounded now (locating the dialog, the search field, OK/Cancel) is here;
the row-pick is marked TODO with the intended approach.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import uiautomation as auto

from .. import uia
from ..exceptions import AmbiguousMatch
from ..session import FakturamaApp

CT = uia.CT


@dataclass
class SelectorResult:
    selected: bool
    cancelled: bool = False
    rows_seen: int = 0


def _find_dialog(app: FakturamaApp, title_substr: str) -> auto.Control:
    """Selector dialogs open as an SWT shell. Match by (sub)title."""
    dlg = app.window.WindowControl(SubName=title_substr)
    if not uia.exists(dlg, 3):
        dlg = auto.WindowControl(searchDepth=2, SubName=title_substr)
    return uia.require(dlg, f"selector dialog '{title_substr}'",
                       hint="capture this dialog with the UIA inspector to finish grounding")


def run_selector(
    app: FakturamaApp,
    open_picker: Callable[[], None],
    dialog_title: str,
    search_term: str,
    row_matches_exact: Callable[[str], bool],
    ocr_rows: Optional[Callable[[auto.Control], list[str]]] = None,
) -> SelectorResult:
    """Open a picker, type the search term, evaluate results.

    Returns selected=True (exact single match chosen + OK), or cancelled=True
    (no exact match -> caller runs its create branch). Raises AmbiguousMatch
    when >1 exact candidate is seen (doc: 'stop for manual review').
    """
    open_picker()
    dlg = _find_dialog(app, dialog_title)

    # Search box: the dialogs have a single Edit near the top; when a live
    # capture confirms a stable Name we switch to that. For now, first Edit.
    search = dlg.EditControl()
    uia.require(search, "selector search field")
    uia.set_text(search, search_term, "selector search", commit=False)
    uia.pause(1.5)  # let the filter settle (doc: 'wait for the list to stabilize')

    # --- row evaluation -------------------------------------------------
    # TODO(needs live capture): if the list turns out to be a JFace TableViewer
    # it will expose ListItem/DataItem rows we can read via Name and click
    # directly. If it is NatTable, use ocr_rows(dlg) to read visible row text
    # and click_point on the first row's rect. The exact-match predicate
    # (row_matches_exact) stays the same either way.
    rows: list[str] = []
    if ocr_rows is not None:
        rows = ocr_rows(dlg)
    exact = [r for r in rows if row_matches_exact(r)]

    if len(exact) > 1:
        _cancel(dlg)
        raise AmbiguousMatch(dialog_title,
                             f"{len(exact)} exact matches for {search_term!r}",
                             context={"rows": rows})
    if len(exact) == 1:
        _select_first_and_ok(dlg)
        return SelectorResult(selected=True, rows_seen=len(rows))

    # No confirmed exact match -> caller creates it.
    _cancel(dlg)
    return SelectorResult(selected=False, cancelled=True, rows_seen=len(rows))


def _select_first_and_ok(dlg: auto.Control) -> None:
    # Placeholder: geometric first-row click then OK. Replace row target once a
    # live capture tells us whether rows are UIA-visible.
    ok = dlg.ButtonControl(Name="OK")
    uia.require(ok, "selector OK button",
                hint="confirm the OK button's Name from a live capture")
    uia.click(ok, "OK")


def _cancel(dlg: auto.Control) -> None:
    cancel = dlg.ButtonControl(Name="Cancel")
    if uia.exists(cancel, 2):
        uia.click(cancel, "Cancel")
    else:
        dlg.SendKeys("{Esc}")
    uia.wait_gone(dlg, timeout=6)
