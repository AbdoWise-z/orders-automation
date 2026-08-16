"""Application session: attach to (or launch) Fakturama, hold the root window,
and manage the multiple editor tabs the flow keeps open at once.

The 'keep the Order tab open while creating a Debtor/VAT/Product' requirement in
the doc maps onto Eclipse editor tabs. In the UIA tree these are TabItemControls
(Names like '*New Order', '*New Debtor'; the '*' means unsaved) and their content
lives in a PaneControl named 'New Order' / 'New Debtor'. We navigate by activating
the tab and scoping field lookups to the content pane.
"""
from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from typing import Optional

import uiautomation as auto

from . import uia
from .config import SETTINGS
from .exceptions import ControlNotFound, FakturamaNotRunning

CT = uia.CT


class FakturamaApp:
    def __init__(self, window: auto.WindowControl):
        self.window = window

    # -- lifecycle -------------------------------------------------------
    @classmethod
    def attach(cls, timeout: float = 8.0) -> "FakturamaApp":
        win = auto.WindowControl(searchDepth=1, SubName=SETTINGS.window_title_substr)
        if not win.Exists(timeout, 0.3):
            raise FakturamaNotRunning(
                "No Fakturama window found. Start Fakturama (and open its workspace) "
                "before running the automation."
            )
        return cls(win)

    @classmethod
    def attach_or_launch(cls) -> "FakturamaApp":
        try:
            return cls.attach(timeout=6.0)
        except FakturamaNotRunning:
            if SETTINGS.attach_only:
                raise
        if not os.path.exists(SETTINGS.fakturama_path):
            raise FakturamaNotRunning(
                f"Fakturama not running and executable not found at "
                f"{SETTINGS.fakturama_path!r}. Set FAKTURAMA_PATH or start it manually."
            )
        subprocess.Popen([SETTINGS.fakturama_path])
        return cls.attach(timeout=SETTINGS.launch_wait)

    def focus(self) -> None:
        # Control has no SetActive; the real Win32 call is what reliably
        # raises Fakturama over an unrelated foreground window (e.g. the
        # terminal driving this REPL) rather than just moving input focus.
        try:
            auto.SetForegroundWindow(self.window.NativeWindowHandle)
        except Exception:
            pass
        try:
            self.window.SetFocus()
        except Exception:
            pass
        uia.pause()

    # -- editors ---------------------------------------------------------
    def editor(self, name: str, timeout: Optional[float] = None) -> auto.Control:
        """The content pane for an open editor, e.g. 'New Order', 'New Debtor'.

        We match on the PaneControl Name (no '*'), which is stable regardless of
        the dirty marker on the tab title.
        """
        pane = self.window.PaneControl(Name=name)
        if not uia.exists(pane, timeout or SETTINGS.short_timeout):
            raise ControlNotFound(
                f"editor pane '{name}'",
                hint="expected an open editor with this title",
            )
        return pane

    def wait_editor(self, name: str) -> auto.Control:
        return self.editor(name, timeout=SETTINGS.editor_wait)

    def activate_editor_tab(self, subname: str) -> None:
        """Bring an editor tab to front by (sub)matching its title. Titles carry
        a '*' while dirty, so we match a substring like 'New Order'."""
        tab = self.window.TabItemControl(SubName=subname)
        uia.require(tab, f"editor tab '{subname}'")
        try:
            tab.GetSelectionItemPattern().Select()
        except Exception:
            tab.Click()
        uia.pause()

    # -- toolbar save ----------------------------------------------------
    def save_active(self) -> None:
        """Click the top-toolbar Save ('Save the current contents'), which saves
        the active editor."""
        uia.click(self.window.ButtonControl(Name="Save the current contents"),
                  "toolbar Save")
        uia.pause(2)  # let the save round-trip to the DB before we verify

    # -- diagnostics -----------------------------------------------------
    def screenshot(self, tag: str = "") -> Optional[str]:
        os.makedirs(SETTINGS.screenshot_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = f"{ts}{('_' + tag) if tag else ''}.png"
        path = os.path.join(SETTINGS.screenshot_dir, name)
        return path if uia.capture(self.window, path) else None
