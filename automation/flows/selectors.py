"""Shared 'search dialog' pattern.

'Select the address' (§2.2), 'Select a product' (§3.3), and the Data > VATs /
terms-of-payment search boxes all follow the same shape: a filter field, a
result list, and OK / Cancel. This module centralises that dance so each flow
just supplies the search term and an OCR/row matcher.

The result list is a NatTable (canvas) — it exposes no rows to UIA, so reading
it and picking the matching row both go through a screenshot + OCR of the
region between the search box and the OK/Cancel row (see ``default_ocr_rows``).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import pytesseract
import uiautomation as auto
from PIL import Image

from .. import uia
from ..config import SETTINGS
from ..exceptions import AmbiguousMatch
from ..grounding import Rect
from ..session import FakturamaApp

CT = uia.CT

# NatTable rows render as plain, left-aligned columns on one text line each;
# "assume a single uniform block of text" keeps Tesseract from trying (and
# failing) to detect paragraphs/columns on its own.
_TESSERACT_CONFIG = "--psm 6"
_OCR_UPSCALE = 3  # NatTable row text is small; upscaling measurably helps OCR.

# Column headers seen across these grids (Select the address / a product /
# the VATs list). Used to drop the header line that OCR reads along with the
# data rows.
_HEADER_WORDS = {
    "no", "first", "name", "company", "zip", "city",          # Select the address
    "item", "description", "stock", "price", "vat",           # Select a product
    "standard", "value",                                      # VATs list
    "document", "date", "state", "total", "printed",          # Data > Documents
}
_HEADER_MIN_HITS = 3


@dataclass
class SelectorResult:
    selected: bool
    cancelled: bool = False
    rows_seen: int = 0


@dataclass
class OcrRow:
    """One OCR-read row: its text (for matching) and screen rect (for clicking)."""
    text: str
    rect: Rect


@dataclass
class OcrWord:
    """One OCR-read word and its screen-x span."""
    text: str
    left: int
    right: int


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
    ocr_rows: Optional[Callable[[auto.Control], list[OcrRow]]] = None,
) -> SelectorResult:
    """Open a picker, type the search term, evaluate results.

    Fakturama auto-closes this dialog the instant the filter narrows to
    exactly one row — that IS 'select it and click OK' already done for us.
    We check for that *before* trying to OCR anything: screenshotting a
    dialog that's already gone is exactly what was producing the empty/
    garbage OCR reads seen earlier, and no amount of OCR tuning fixes that.

    Returns selected=True (exact single match — either Fakturama's own
    auto-close, or our click+OK when several rows were visible), or
    cancelled=True (no exact match -> caller runs its create branch). Raises
    AmbiguousMatch when >1 exact candidate is seen (doc: 'stop for manual
    review').
    """
    open_picker()
    dlg = _find_dialog(app, dialog_title)

    # Search box: the dialogs have a single Edit near the top; when a live
    # capture confirms a stable Name we switch to that. For now, first Edit.
    search = dlg.EditControl()
    uia.require(search, "selector search field")
    uia.set_text(search, search_term, "selector search", commit=False)
    uia.pause(1.5)  # let the filter settle (doc: 'wait for the list to stabilize')

    if not uia.exists(dlg, 1):
        print(f"[selector] {dialog_title!r} auto-closed on a single match for {search_term!r}")
        return SelectorResult(selected=True, rows_seen=1)

    # --- row evaluation (dialog still open: 0 or >1 matches) --------------
    reader = ocr_rows or default_ocr_rows
    rows = reader(dlg)
    if not rows:
        # An empty read is ambiguous: genuinely no match, or the results panel
        # (or, for a just-created record, the search index) hadn't finished
        # rendering yet. Give it one more beat before trusting 'no match'.
        uia.pause(2.0)
        rows = reader(dlg)
    exact = [r for r in rows if row_matches_exact(r.text)]

    print([r.text for r in rows])
    if len(exact) > 1:
        _cancel(dlg)
        raise AmbiguousMatch(
            f"{len(exact)} exact matches for {search_term!r} in '{dialog_title}'",
            user_message=(
                f"Found {len(exact)} matching records for “{search_term}” — "
                "please resolve manually."
            ),
            context={"rows": [r.text for r in rows], "search_term": search_term},
            step=dialog_title,
        )
    if len(exact) == 1:
        _select_and_ok(dlg, exact[0].rect)
        return SelectorResult(selected=True, rows_seen=len(rows))

    # No confirmed exact match -> caller creates it.
    _cancel(dlg)
    return SelectorResult(selected=False, cancelled=True, rows_seen=len(rows))


def _select_and_ok(dlg: auto.Control, row_rect: Rect) -> None:
    """Physically click the OCR-matched row (NatTable exposes no clickable
    row control), then confirm with OK."""
    auto.Click(row_rect.cx, row_rect.cy)
    uia.pause()
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


# --------------------------------------------------------------------------- #
# OCR row / column reading
#
# Generic over any NatTable canvas: selector-dialog result lists (search box
# above, OK/Cancel below), the VATs list (search box, no OK/Cancel), and the
# Items grid on the Order editor (no search box, no OK/Cancel — just header +
# rows starting at the control's own top edge).
# --------------------------------------------------------------------------- #
def default_ocr_rows(dlg: auto.Control) -> list[OcrRow]:
    """Selector-dialog / VATs-list rows: crop between the search box and
    OK/Cancel (or the control's own bottom, if there's no OK button)."""
    search = dlg.EditControl()
    if not uia.exists(search, 1):
        return []
    dlg_rect = dlg.BoundingRectangle
    top = max(0, search.BoundingRectangle.bottom - dlg_rect.top)
    ok = dlg.ButtonControl(Name="OK")
    bottom = (ok.BoundingRectangle.top - dlg_rect.top) if uia.exists(ok, 1) else None
    return read_rows(dlg, top=top, bottom=bottom)


def read_rows(ctrl: auto.Control, *, top: int = 0, bottom: Optional[int] = None,
              left: int = 0, right: Optional[int] = None) -> list[OcrRow]:
    """OCR ctrl (or its sub-region, in ctrl-relative pixels) into one OcrRow
    per detected text line, dropping any header-looking line.

    ``left`` matters for views that put a navigation tree beside their grid
    (Data > Documents): without it the tree's labels OCR as extra 'rows'.
    """
    captured = _capture_image(ctrl, top=top, bottom=bottom, left=left, right=right)
    if captured is None:
        print("[OCR] read_rows: nothing to capture (unbounded region)")
        return []
    image, origin = captured
    rows = _rows_from_ocr_data(_run_ocr(image), origin)
    print(f"[OCR] read_rows -> {[r.text for r in rows]}")
    return rows


def header_words(ctrl: auto.Control) -> list[OcrWord]:
    """The words of ctrl's topmost text line (a NatTable's header row),
    ordered left→right, each with its screen-x span.

    Callers get spans rather than just centres because a short, left-aligned
    header ('VAT') has its centre sitting near its column's LEFT EDGE — right
    on the boundary with the previous column — so clicking a word centre is
    nearly a coin flip between the two cells. Derive column spans from
    consecutive header lefts and click those midpoints instead.
    """
    captured = _capture_image(ctrl)
    if captured is None:
        print("[OCR] header_words: nothing to capture (unbounded region)")
        return []
    image, (ox, _oy) = captured
    data = _run_ocr(image)
    print(f"[OCR] header_words raw: {[w for w in data['text'] if w and w.strip()]}")

    # Group by Tesseract's own (block, par, line) segmentation — the same
    # mechanism _rows_from_ocr_data relies on to separate rows — rather than a
    # raw pixel-distance tolerance, which can merge the header into a data row
    # that sits close beneath it (e.g. a grid with a single item line).
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, word in enumerate(data["text"]):
        if not word or not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(i)
    if not groups:
        print("[OCR] header_words: no recognizable text at all")
        return []
    header_key = min(groups, key=lambda k: min(data["top"][i] for i in groups[k]))

    words: list[OcrWord] = []
    for i in groups[header_key]:
        text = data["text"][i].strip()
        # NatTable's vertical column borders OCR as their own 'words' — and
        # not always as '|': Tesseract frequently reads them as 'l' or 'I',
        # which no alpha-only filter would catch. No real header is a single
        # character, so length is the reliable discriminator.
        if len(text) < 2 or not any(ch.isalpha() for ch in text):
            continue
        words.append(OcrWord(
            text=text.strip(".:").lower(),
            left=ox + data["left"][i] // _OCR_UPSCALE,
            right=ox + (data["left"][i] + data["width"][i]) // _OCR_UPSCALE,
        ))
    words.sort(key=lambda w: w.left)
    print(f"[OCR] header_words -> {[(w.text, w.left, w.right) for w in words]}")
    return words


def _run_ocr(image) -> dict:
    # Re-applied on every call (not just at import) so a config change or a
    # long-lived process that imported this module before tesseract_cmd was
    # set/changed can't silently keep using a stale value.
    if SETTINGS.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = SETTINGS.tesseract_cmd
    try:
        return pytesseract.image_to_data(
            image, config=_TESSERACT_CONFIG, output_type=pytesseract.Output.DICT,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError(
            f"Tesseract OCR not found at "
            f"{pytesseract.pytesseract.tesseract_cmd!r} (SETTINGS.tesseract_cmd="
            f"{SETTINGS.tesseract_cmd!r}). Install Tesseract-OCR "
            "(https://github.com/UB-Mannheim/tesseract/wiki) and/or set the "
            "TESSERACT_CMD environment variable to its executable path."
        ) from exc


def _capture_image(ctrl: auto.Control, *, top: int = 0, bottom: Optional[int] = None,
                   left: int = 0, right: Optional[int] = None):
    """Crop ctrl to [left, right) x [top, bottom) (ctrl-relative pixels; None
    means ctrl's own edge) and upscale for OCR. Returns (PIL.Image, origin)
    where origin is the (screen_x, screen_y) of the crop's top-left corner, or
    None if the region can't be bounded."""
    rect = ctrl.BoundingRectangle
    if bottom is None:
        bottom = rect.bottom - rect.top
    if right is None:
        right = rect.right - rect.left
    height = bottom - top
    width = right - left
    if height <= 0 or width <= 0:
        return None

    # CaptureToImage grabs whatever's actually on screen at these pixel
    # coordinates — if another window (e.g. the terminal driving this REPL)
    # has stolen focus and overlaps this region, OCR reads garbage from it
    # instead of Fakturama. Force Fakturama to the foreground first.
    try:
        top_level = ctrl.GetTopLevelControl()
        auto.SetForegroundWindow((top_level or ctrl).NativeWindowHandle)
        uia.pause(0.3)
    except Exception:
        pass

    os.makedirs(SETTINGS.screenshot_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(SETTINGS.screenshot_dir, f"ocr_capture_{ts}.png")
    if not ctrl.CaptureToImage(path, x=left, y=top, width=width, height=height):
        print(f"[OCR] CaptureToImage failed for rect={rect} "
              f"crop x=[{left},{right}) y=[{top},{bottom})")
        return None
    print(f"[OCR] captured {path} (crop x=[{left},{right}) y=[{top},{bottom}) of {rect})")

    image = Image.open(path).convert("L")
    if _OCR_UPSCALE != 1:
        image = image.resize(
            (image.width * _OCR_UPSCALE, image.height * _OCR_UPSCALE), Image.LANCZOS,
        )
    return image, (rect.left + left, rect.top + top)


def _rows_from_ocr_data(data: dict, origin: tuple[int, int]) -> list[OcrRow]:
    """Group Tesseract's word-level output back into rows (one NatTable row
    per Tesseract text line), keeping each row's bounding rect for clicking."""
    ox, oy = origin
    groups: dict[tuple[int, int, int], list[int]] = {}
    for i, word in enumerate(data["text"]):
        if not word or not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append(i)

    rows: list[OcrRow] = []
    for key, idxs in groups.items():
        idxs.sort(key=lambda i: data["left"][i])
        text = _normalize(" ".join(data["text"][i] for i in idxs))
        if not text or _looks_like_header(text):
            continue

        lefts = [data["left"][i] for i in idxs]
        tops = [data["top"][i] for i in idxs]
        rights = [data["left"][i] + data["width"][i] for i in idxs]
        bottoms = [data["top"][i] + data["height"][i] for i in idxs]
        rect = Rect(
            left=ox + min(lefts) // _OCR_UPSCALE,
            top=oy + min(tops) // _OCR_UPSCALE,
            right=ox + max(rights) // _OCR_UPSCALE,
            bottom=oy + max(bottoms) // _OCR_UPSCALE,
        )
        rows.append(OcrRow(text=text, rect=rect))

    rows.sort(key=lambda r: r.rect.top)
    return rows


def _looks_like_header(text: str) -> bool:
    words = {w.strip(".,").lower() for w in text.split()}
    return len(words & _HEADER_WORDS) >= _HEADER_MIN_HITS


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
