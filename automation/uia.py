"""Low-level UI Automation primitives.

This is the ONLY module that imports `uiautomation`. Every other module asks
for controls in semantic terms (widgets.py) or by flow intent (flows/*), so if
SWT's tree surprises us we change grounding in one place.

Design notes specific to Fakturama (Eclipse RCP / SWT):
  * AutomationId == the window HANDLE in decimal, so it is NOT stable across
    launches. We never ground on AutomationId. We use Name (stable on labelled
    fields), ControlType/ClassName, and structural/geometric position.
  * Many links/icons are SWT widgets exposing only LegacyIAccessiblePattern
    (no InvokePattern). For those we fall back to a physical Click().
  * Edits are updated by paste, not ValuePattern.SetValue, so SWT/JFace modify
    listeners fire and the data binding actually commits.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Optional

import uiautomation as auto

from .config import SETTINGS
from .exceptions import ControlNotFound

auto.SetGlobalSearchTimeout(SETTINGS.search_timeout)

CT = auto.ControlType


# --------------------------------------------------------------------------- #
# timing / existence
# --------------------------------------------------------------------------- #
def pause(mult: float = 1.0) -> None:
    time.sleep(SETTINGS.action_pause * mult)


def exists(ctrl: auto.Control, timeout: Optional[float] = None) -> bool:
    return ctrl.Exists(SETTINGS.short_timeout if timeout is None else timeout, 0.2)


def require(ctrl: auto.Control, what: str, timeout: Optional[float] = None,
            hint: Optional[str] = None) -> auto.Control:
    if not exists(ctrl, timeout):
        raise ControlNotFound(what, hint=hint)
    return ctrl


def wait_gone(ctrl: auto.Control, timeout: float = 10.0) -> bool:
    """Wait for a control (e.g. a closed dialog) to disappear."""
    end = time.time() + timeout
    while time.time() < end:
        if not ctrl.Exists(0.3, 0.1):
            return True
        time.sleep(0.2)
    return False


# --------------------------------------------------------------------------- #
# structural helpers (defeat empty-Name / unstable-AutomationId fields)
# --------------------------------------------------------------------------- #
def children(ctrl: auto.Control) -> list[auto.Control]:
    return ctrl.GetChildren()


def parent(ctrl: auto.Control) -> Optional[auto.Control]:
    return ctrl.GetParentControl()


def of_type(scope: auto.Control, control_type) -> list[auto.Control]:
    return [c for c in scope.GetChildren() if c.ControlType == control_type]


def find_by_value_regex(scope: auto.Control, pattern: str,
                        timeout: Optional[float] = None) -> Optional[auto.Control]:
    """Find an Edit inside `scope` whose current value matches `pattern`.

    Grounds the Date field (only edit holding a 'MMM d, yyyy' value) without
    relying on its position or its empty Name.
    """
    rx = re.compile(pattern)

    def cmp(c: auto.Control, _depth: int) -> bool:
        try:
            v = c.GetValuePattern().Value
        except Exception:
            return False
        return bool(v and rx.match(v))
    
    ctrl = scope.EditControl(Compare=cmp)
    return ctrl if exists(ctrl, timeout) else None


def find_by_combo_value_regex(scope: auto.Control, pattern: str,
                        timeout: Optional[float] = None) -> Optional[auto.Control]:
    """Find a ComboBox inside `scope` whose current value matches `pattern`.

    Grounds the Date field (only edit holding a 'MMM d, yyyy' value) without
    relying on its position or its empty Name.
    """
    rx = re.compile(pattern)

    def cmp(c: auto.Control, _depth: int) -> bool:
        try:
            v = c.GetValuePattern().Value
        except Exception:
            return False
        return bool(v and rx.match(v))
    
    ctrl = scope.ComboBoxControl(Compare=cmp)
    return ctrl if exists(ctrl, timeout) else None


def images_under_label(scope: auto.Control, label: str) -> list[auto.Control]:
    """Return the ImageControls that share a parent with the Static `label`,
    sorted top→bottom.

    Grounds the 'upper vs lower green +' distinction the doc keeps making:
      * Addresses: [0] = existing-contact picker, [1] = create-new (+)
      * Items:     [0] = product selector (topmost icon)
    """
    lbl = require(scope.TextControl(Name=label), f"label '{label}'",
                  hint="re-capture this editor with the UIA inspector if it moved")
    imgs = of_type(lbl.GetParentControl(), CT.ImageControl)
    imgs.sort(key=lambda c: c.BoundingRectangle.top)
    if not imgs:
        raise ControlNotFound(f"icons under label '{label}'")
    return imgs


def find_by_name(scope: auto.Control, name: str, control_type=None,
                 timeout: Optional[float] = None) -> Optional[auto.Control]:
    kw = {"Name": name}
    ctor = _ctor_for(scope, control_type)
    ctrl = ctor(**kw)
    return ctrl if exists(ctrl, timeout) else None


def _ctor_for(scope: auto.Control, control_type) -> Callable[..., auto.Control]:
    mapping = {
        CT.EditControl: scope.EditControl,
        CT.ButtonControl: scope.ButtonControl,
        CT.TextControl: scope.TextControl,
        CT.ComboBoxControl: scope.ComboBoxControl,
        CT.PaneControl: scope.PaneControl,
        CT.ImageControl: scope.ImageControl,
        CT.TabItemControl: scope.TabItemControl,
        CT.GroupControl: scope.GroupControl,
        CT.CheckBoxControl: scope.CheckBoxControl,
        CT.ListItemControl: scope.ListItemControl,
    }
    return mapping.get(control_type, scope.Control)


# --------------------------------------------------------------------------- #
# interaction
# --------------------------------------------------------------------------- #
def click(ctrl: auto.Control, what: str = "", timeout: Optional[float] = None) -> None:
    require(ctrl, what or "control", timeout)
    try:
        ctrl.GetInvokePattern().Invoke()          # real Buttons / toolbar items
    except Exception:
        try:
            ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()  # SWT links
        except Exception:
            ctrl.Click()                          # last resort: physical click
    pause()


def click_point(ctrl: auto.Control, x_ratio: float = 0.5, y_ratio: float = 0.5,
                what: str = "") -> None:
    """Physical click at a point inside the control's rect (for canvas/NatTable
    rows that expose no clickable children)."""
    require(ctrl, what or "region")
    r = ctrl.BoundingRectangle
    x = int(r.left + (r.right - r.left) * x_ratio)
    y = int(r.top + (r.bottom - r.top) * y_ratio)
    auto.Click(x, y)
    pause()


def get_text(ctrl: auto.Control) -> str:
    for getter in (lambda: ctrl.GetValuePattern().Value,
                   lambda: ctrl.GetLegacyIAccessiblePattern().Value):
        try:
            v = getter()
            if v is not None:
                return v
        except Exception:
            continue
    return ctrl.Name or ""


def set_text(ctrl: auto.Control, text, what: str = "", commit: bool = True) -> None:
    """Clear then paste `text`. Paste (rather than ValuePattern.SetValue) ensures
    SWT modify-listeners fire so the model updates. Clipboard is restored."""
    require(ctrl, what or "edit")
    text = "" if text is None else str(text)
    ctrl.SetFocus()
    pause(0.5)
    ctrl.SendKeys("{Ctrl}a", waitTime=0.02)
    ctrl.SendKeys("{Delete}", waitTime=0.02)
    if text:
        saved = None
        try:
            saved = auto.GetClipboardText()
        except Exception:
            pass
        auto.SetClipboardText(text)
        ctrl.SendKeys("{Ctrl}v", waitTime=0.02)
        pause(0.6)
        if saved is not None:
            try:
                auto.SetClipboardText(saved)
            except Exception:
                pass
    if commit:
        ctrl.SendKeys("{Tab}", waitTime=0.02)
    pause()


# --------------------------------------------------------------------------- #
# combos
# --------------------------------------------------------------------------- #
def combo_value(ctrl: auto.Control) -> str:
    try:
        return ctrl.GetValuePattern().Value or ""
    except Exception:
        return ""


def combo_select(ctrl: auto.Control, value: str, what: str = "") -> None:
    """Best-effort combo selection. Expands, then clicks the matching ListItem.

    NOTE: Fakturama mixes native Windows combos (ClassName 'ComboBox') and SWT
    CCombos (ClassName 'SWT_Window0'). This path is validated for the native
    ones (Country/Salutation/payment-code). SWT CCombo popups render into a
    transient window; if selection fails here we type the value + Enter as a
    fallback. Re-test each combo as we reach it.
    """
    require(ctrl, what or "combo")
    try:
        ecp = ctrl.GetExpandCollapsePattern()
        ecp.Expand()
        pause()
        item = ctrl.ListItemControl(Name=value)
        if not exists(item, 2):
            item = auto.ListItemControl(Name=value)  # popup may parent to desktop
            ecp.Collapse()
        if exists(item, 2):
            try:
                item.GetSelectionItemPattern().Select()
            except Exception:
                item.Click()
            pause()
            ecp.Collapse()
            return
    except Exception:
        pass
    # fallback: type + Enter (works for editable combos like Country)
    ctrl.SetFocus()
    ctrl.SendKeys("{Ctrl}a", waitTime=0.02)
    auto.SetClipboardText(value)
    ctrl.SendKeys("{Ctrl}v", waitTime=0.02)
    ctrl.SendKeys("{Enter}", waitTime=0.02)
    pause()


# --------------------------------------------------------------------------- #
# capture (for OCR fallback on NatTable grids + debugging)
# --------------------------------------------------------------------------- #
def capture(ctrl: auto.Control, path: str) -> bool:
    try:
        return bool(ctrl.CaptureToImage(path))
    except Exception:
        return False
