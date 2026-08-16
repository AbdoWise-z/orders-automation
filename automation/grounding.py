"""The grounding layer: the *only* module that imports ``uiautomation``.

Why it exists
-------------
Every flow describes *what* it wants ("the Edit labelled 'Street'", "the upper
icon in the Addresses block") and this module resolves it to a live control.
When SWT's tree surprises us, the fix lives here, not scattered across flows.

Grounding contract for this app
-------------------------------
* NEVER ground on numeric ``AutomationId``. In this SWT build the AutomationId
  is just the window handle (e.g. Customer-ID edit ``0x20A34`` == 133684), which
  is unstable across restarts and re-opened editors.
* Prefer ``Name`` + ``ControlType``. SWT copies each field's label into the
  accessible Name, so labelled fields (Street, Company, Cust.Ref., ...) are
  reliable.
* For unlabelled controls (No., Date, First/Last name, icons) anchor on the
  nearest labelled ``Static`` and pick by geometry.
* Last resort: physical click at a rect centre (for icons and NatTable cells)
  and OCR of a screen region (for the invisible grids).
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Pattern

import uiautomation as auto  # Windows-only; imported lazily by callers on Win 11

from .exceptions import AmbiguousMatch, ControlNotFound, ValueNotApplied

log = logging.getLogger("automation.grounding")

DEFAULT_TIMEOUT = 10.0
POLL_INTERVAL = 0.25

Control = "auto.Control"          # type alias for readability
Predicate = Callable[[object], bool]

# Optional OCR hook: set to a callable(PIL.Image) -> str to enable grid reads.
# Kept out of import-time deps so the package imports without PIL/tesseract.
OCR_HOOK: Optional[Callable[[object], str]] = None


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def cx(self) -> int:
        return (self.left + self.right) // 2

    @property
    def cy(self) -> int:
        return (self.top + self.bottom) // 2

    def voverlap(self, other: "Rect") -> int:
        return max(0, min(self.bottom, other.bottom) - max(self.top, other.top))

    def hoverlap(self, other: "Rect") -> int:
        return max(0, min(self.right, other.right) - max(self.left, other.left))


def rect_of(ctrl) -> Rect:
    r = ctrl.BoundingRectangle
    return Rect(r.left, r.top, r.right, r.bottom)


# --------------------------------------------------------------------------- #
# Matching / searching
# --------------------------------------------------------------------------- #
def _matches(
    ctrl,
    control_type: Optional[str],
    name: Optional[str],
    name_re: Optional[Pattern],
    class_name: Optional[str],
    predicate: Optional[Predicate],
) -> bool:
    try:
        if control_type and ctrl.ControlTypeName != control_type:
            return False
        cname = ctrl.Name or ""
        if name is not None and cname != name:
            return False
        if name_re is not None and not name_re.search(cname):
            return False
        if class_name is not None and ctrl.ClassName != class_name:
            return False
        if predicate is not None and not predicate(ctrl):
            return False
        return True
    except Exception:  # a control can vanish mid-walk; treat as non-match
        return False


def _walk(root, max_depth: int) -> Iterable:
    """Breadth-first walk yielding descendants in stable document order.

    Uses ``GetChildren`` (reliable on SWT) rather than ``WalkControl`` so
    behaviour is identical across ``uiautomation`` versions.
    """
    q = deque()
    for child in _children(root):
        q.append((child, 1))
    while q:
        ctrl, depth = q.popleft()
        yield ctrl
        if depth < max_depth:
            for child in _children(ctrl):
                q.append((child, depth + 1))


def _children(ctrl) -> list:
    try:
        return ctrl.GetChildren() or []
    except Exception:
        return []


def find_all(
    root,
    *,
    control_type: Optional[str] = None,
    name: Optional[str] = None,
    name_re: Optional[str] = None,
    class_name: Optional[str] = None,
    predicate: Optional[Predicate] = None,
    max_depth: int = 40,
) -> list:
    rx = re.compile(name_re) if name_re else None
    return [
        c for c in _walk(root, max_depth)
        if _matches(c, control_type, name, rx, class_name, predicate)
    ]


def find(
    root,
    *,
    control_type: Optional[str] = None,
    name: Optional[str] = None,
    name_re: Optional[str] = None,
    class_name: Optional[str] = None,
    predicate: Optional[Predicate] = None,
    max_depth: int = 40,
    timeout: float = 0.0,
):
    """Return the first matching control, or None. Waits up to ``timeout``."""
    deadline = time.time() + timeout
    while True:
        hits = find_all(
            root, control_type=control_type, name=name, name_re=name_re,
            class_name=class_name, predicate=predicate, max_depth=max_depth,
        )
        if hits:
            return hits[0]
        if time.time() >= deadline:
            return None
        time.sleep(POLL_INTERVAL)


def require(
    root,
    *,
    what: str,
    timeout: float = DEFAULT_TIMEOUT,
    **kw,
):
    """Like ``find`` but raises ``ControlNotFound`` with a useful message."""
    ctrl = find(root, timeout=timeout, **kw)
    if ctrl is None:
        raise ControlNotFound(
            f"Could not find {what}",
            user_message=f"Could not find “{what}” on screen.",
            context={"query": {k: v for k, v in kw.items() if v is not None}},
        )
    return ctrl


def require_unique(root, *, what: str, **kw):
    """Require exactly one match; raise AmbiguousMatch if more than one."""
    hits = find_all(root, **kw)
    if not hits:
        raise ControlNotFound(f"Could not find {what}", user_message=f"Could not find “{what}”.")
    if len(hits) > 1:
        raise AmbiguousMatch(
            f"Expected one {what}, found {len(hits)}",
            user_message=f"Found {len(hits)} candidates for “{what}” where one was expected.",
        )
    return hits[0]


def wait_until(predicate: Callable[[], bool], *, what: str, timeout: float = DEFAULT_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)
    return False


# --------------------------------------------------------------------------- #
# Label-anchored field resolution
# --------------------------------------------------------------------------- #
def field(root, label: str, *, kind: str = "edit", timeout: float = DEFAULT_TIMEOUT):
    """Resolve an input by its label.

    Strategy: (1) SWT usually copies the label onto the field's Name, so try a
    control of the right type whose Name == label. (2) Otherwise anchor on the
    ``Static`` labelled ``label`` and pick the nearest input to its right, then
    below.
    """
    ctype = {"edit": "EditControl", "combo": "ComboBoxControl"}[kind]

    labelled = find(root, control_type=ctype, name=label, timeout=0.0)
    if labelled is not None:
        return labelled

    static = require(
        root, control_type="TextControl", name=label, timeout=timeout,
        what=f"label '{label}'",
    )
    return _nearest_input(root, static, ctype, label)


def _nearest_input(root, static, ctype: str, label: str):
    anchor = rect_of(static)
    candidates = find_all(root, control_type=ctype)
    best, best_key = None, None
    for c in candidates:
        r = rect_of(c)
        if r.voverlap(anchor) > 0 and r.left >= anchor.left:          # same row, to the right
            key = (0, r.left - anchor.right if r.left >= anchor.right else 0, abs(r.top - anchor.top))
        elif r.top >= anchor.bottom and r.hoverlap(anchor) > 0:        # directly below
            key = (1, r.top - anchor.bottom, abs(r.left - anchor.left))
        else:
            continue
        if best_key is None or key < best_key:
            best, best_key = c, key
    if best is None:
        raise ControlNotFound(
            f"No {ctype} near label '{label}'",
            user_message=f"Could not find the input next to “{label}”.",
        )
    return best


def edit_below_after(root, static, *, index: int = 0):
    """For grouped rows (e.g. First/Last name, ZIP/City) that share one label:
    return the ``index``-th unlabelled Edit on the same row as ``static``,
    ordered left-to-right."""
    anchor = rect_of(static)
    row = [
        c for c in find_all(root, control_type="EditControl")
        if rect_of(c).voverlap(anchor) > 0 and rect_of(c).left >= anchor.left
    ]
    row.sort(key=lambda c: rect_of(c).left)
    if index >= len(row):
        raise ControlNotFound(
            f"No Edit #{index} on row of label '{static.Name}'",
            user_message=f"Could not find field #{index + 1} next to “{static.Name}”.",
        )
    return row[index]


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
def read_value(ctrl) -> Optional[str]:
    for getter in ("GetValuePattern", "GetLegacyIAccessiblePattern"):
        try:
            pat = getattr(ctrl, getter)()
            if pat is not None:
                return pat.Value
        except Exception:
            continue
    return None


def click(ctrl, *, what: str = "control") -> None:
    """Invoke if possible; else select; else physically click the centre.

    Icons here are ``ImageControl``/``Static`` with no InvokePattern, so the
    physical-click fallback is the common path for them.
    """
    for getter, method in (("GetInvokePattern", "Invoke"),
                           ("GetSelectionItemPattern", "Select")):
        try:
            pat = getattr(ctrl, getter)()
            if pat is not None:
                getattr(pat, method)()
                return
        except Exception:
            continue
    try:
        ctrl.Click(simulateMove=False, waitTime=0.1)
    except Exception as exc:
        raise ControlNotFound(
            f"Could not click {what}: {exc}",
            user_message=f"Could not click “{what}”.",
        ) from exc


def click_rect(rect: Rect, *, dx: int = 0, dy: int = 0) -> None:
    """Physical click at a rect centre (+offset). For icons and grid cells."""
    auto.Click(rect.cx + dx, rect.cy + dy, waitTime=0.1)


def set_text(ctrl, value: Optional[str], *, verify: bool = True,
             what: str = "field", timeout: float = 4.0) -> None:
    """Set an Edit's text and verify the read-back.

    Prefers ``ValuePattern.SetValue`` (all SWT Edits here expose ValuePattern);
    falls back to focus + select-all + typed keys with proper escaping.
    """
    text = "" if value is None else str(value)

    try:
        vp = ctrl.GetValuePattern()
        if vp is not None:
            vp.SetValue(text)
    except Exception:
        pass

    if verify and _norm(read_value(ctrl)) == _norm(text):
        return

    try:
        ctrl.SetFocus()
        auto.SendKeys("{Ctrl}a{Delete}", waitTime=0.03)
        if text:
            auto.SendKeys(_escape_keys(text), waitTime=0.01)
    except Exception as exc:
        raise ValueNotApplied(
            f"Could not type into {what}: {exc}",
            user_message=f"Could not enter a value into “{what}”.",
        ) from exc

    if not verify:
        return
    if wait_until(lambda: _norm(read_value(ctrl)) == _norm(text),
                  what=f"{what} == {text!r}", timeout=timeout):
        return
    raise ValueNotApplied(
        f"{what} did not accept {text!r}; reads {read_value(ctrl)!r}",
        user_message=f"“{what}” would not accept the value “{text}”.",
        context={"expected": text, "actual": read_value(ctrl)},
    )


def set_combo(ctrl, value: str, *, root=None, what: str = "dropdown",
              timeout: float = 4.0) -> None:
    """Select ``value`` in a combo. Tries SetValue, then expand + pick item.

    ``root`` (the top window) is needed to find the popup list, which SWT often
    renders outside the combo's own subtree.
    """
    if _norm(read_value(ctrl)) == _norm(value):
        return
    try:
        vp = ctrl.GetValuePattern()
        if vp is not None:
            vp.SetValue(value)
            if _norm(read_value(ctrl)) == _norm(value):
                return
    except Exception:
        pass

    try:
        ecp = ctrl.GetExpandCollapsePattern()
        ecp.Expand()
    except Exception:
        click(ctrl, what=what)

    search_root = root or ctrl
    item = find(search_root, control_type="ListItemControl", name=value, timeout=3.0) \
        or find(search_root, control_type="TextControl", name=value, timeout=1.0)
    if item is None:
        raise ValueNotApplied(
            f"Combo {what}: no option {value!r}",
            user_message=f"“{value}” was not available in the “{what}” dropdown.",
            context={"wanted": value, "current": read_value(ctrl)},
        )
    click(item, what=f"{what} option {value!r}")
    if not wait_until(lambda: _norm(read_value(ctrl)) == _norm(value),
                      what=f"{what} == {value!r}", timeout=timeout):
        raise ValueNotApplied(
            f"Combo {what} did not switch to {value!r}",
            user_message=f"The “{what}” dropdown did not switch to “{value}”.",
        )


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def _norm(s: Optional[str]) -> str:
    return (s or "").strip()


_SENDKEYS_SPECIAL = set("{}()+^%~#")


def _escape_keys(text: str) -> str:
    """Escape characters that ``uiautomation.SendKeys`` treats as modifiers/
    grouping (so '+49' types a plus, not Shift+4-9)."""
    return "".join("{" + ch + "}" if ch in _SENDKEYS_SPECIAL else ch for ch in text)
