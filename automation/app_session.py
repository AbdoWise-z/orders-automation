"""Session layer: own the Fakturama top window and the coarse navigation the
flows need — top-toolbar buttons, the left navigation list, editor tabs, and
modal dialogs. All fine-grained control resolution is delegated to
``grounding``; this module knows the *map* of the app, not individual fields.

Editor identity note
--------------------
Editors are ``TabItem``s in the top-right editor ``TabControl``. Their tab Name
carries a leading ``*`` while dirty (``*New Order``) and changes after save, so
we match tabs by regex on Name, and we re-resolve the editor's *content pane* by
its stable semantic Name (``New Order``, ``New Debtor``, ``New Invoice``) every
time we need it — never by handle.
"""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Optional

import uiautomation as auto

from . import grounding as g
from .exceptions import ControlNotFound, FakturamaNotRunning

log = logging.getLogger("automation.session")

WINDOW_CLASS = "SWT_Window0"
WINDOW_NAME_SUB = "Fakturama"

# Top-toolbar button accessible Names (stable — they come from the tooltips).
BTN_NEW_ORDER = "Create: New Order"
BTN_NEW_INVOICE_TOOLBAR = "Create: New Invoice"   # NOTE: avoid for the linked flow
BTN_SAVE = "Save the current contents"

# Editor content-pane semantic Names.
PANE_NEW_ORDER = "New Order"
PANE_NEW_DEBTOR = "New Debtor"
PANE_NEW_INVOICE = "New Invoice"


class FakturamaSession:
    def __init__(self, exe_path: Optional[str] = None) -> None:
        self.exe_path = exe_path
        self.window = None

    # -- lifecycle --------------------------------------------------------- #
    def attach(self, timeout: float = 15.0) -> "FakturamaSession":
        deadline = time.time() + timeout
        while time.time() < deadline:
            win = auto.WindowControl(
                searchDepth=1, ClassName=WINDOW_CLASS, SubName=WINDOW_NAME_SUB
            )
            if win.Exists(0, 0):
                self.window = win
                log.info("Attached to Fakturama: %s", win.Name)
                return self
            time.sleep(0.4)
        raise FakturamaNotRunning("Fakturama top window not found")

    def launch_and_attach(self, timeout: float = 60.0) -> "FakturamaSession":
        if not self.exe_path:
            raise FakturamaNotRunning(
                "No Fakturama executable path configured",
                user_message="Set the Fakturama executable path before launching.",
            )
        log.info("Launching Fakturama: %s", self.exe_path)
        subprocess.Popen([self.exe_path])
        return self.attach(timeout=timeout)

    def activate(self) -> None:
        self._require_window()
        try:
            self.window.SetActive()
        except Exception:
            pass
        self.window.SetFocus()

    # -- top toolbar ------------------------------------------------------- #
    def toolbar_button(self, name: str, timeout: float = g.DEFAULT_TIMEOUT):
        self._require_window()
        return g.require(
            self.window, control_type="ButtonControl", name=name,
            timeout=timeout, what=f"toolbar button '{name}'",
        )

    def click_toolbar(self, name: str) -> None:
        g.click(self.toolbar_button(name), what=f"toolbar '{name}'")

    def save_active_editor(self) -> None:
        """Click the global Save. The active editor is the target (step 4.4)."""
        self.activate()
        g.click(self.toolbar_button(BTN_SAVE), what="Save")

    # -- left navigation --------------------------------------------------- #
    def open_nav(self, name: str, timeout: float = g.DEFAULT_TIMEOUT) -> None:
        """Open a list editor from the left Navigation View by clicking its
        label (e.g. 'VATs', 'terms of payment', 'Documents', 'Products',
        'Debtors'). Equivalent to the doc's 'Data > …' menu path, but the nav
        labels are plain clickable Text items and more reliable to reach."""
        self._require_window()
        nav = g.require(
            self.window, control_type="PaneControl", name="Navigation View",
            timeout=timeout, what="Navigation View",
        )
        label = g.require(
            nav, control_type="TextControl", name=name, timeout=timeout,
            what=f"navigation item '{name}'",
        )
        g.click_rect(g.rect_of(label))   # Text nav items have no InvokePattern

    # -- application menu (fallback path for anything not in the nav) ------ #
    def open_menu(self, top: str, item: str, timeout: float = g.DEFAULT_TIMEOUT) -> None:
        self._require_window()
        top_item = g.require(
            self.window, control_type="MenuItemControl", name=top,
            timeout=timeout, what=f"menu '{top}'",
        )
        try:
            top_item.GetExpandCollapsePattern().Expand()
        except Exception:
            g.click(top_item, what=f"menu '{top}'")
        root = auto.GetRootControl()
        child = g.require(
            root, control_type="MenuItemControl", name=item, timeout=timeout,
            what=f"menu item '{top} > {item}'",
        )
        g.click(child, what=f"menu '{top} > {item}'")

    # -- editors ----------------------------------------------------------- #
    def new_order(self, timeout: float = 15.0):
        """Open a New Order and return its content pane (step 1.3)."""
        self.click_toolbar(BTN_NEW_ORDER)
        pane = self.editor_pane(PANE_NEW_ORDER, timeout=timeout)
        log.info("New Order editor open")
        return pane

    def editor_pane(self, name: str, timeout: float = g.DEFAULT_TIMEOUT):
        """Re-resolve an editor's content pane by its semantic Name. Stable
        across saves and handle churn."""
        self._require_window()
        return g.require(
            self.window, control_type="PaneControl", name=name,
            timeout=timeout, what=f"'{name}' editor",
        )

    def activate_editor_tab(self, name_re: str, timeout: float = g.DEFAULT_TIMEOUT) -> None:
        """Bring an editor tab to the front by regex on its (possibly '*'-prefixed
        or renamed) tab title. Used to return to the still-open Order after
        diving into New Debtor / dialogs (steps 1.8, 2.12)."""
        self._require_window()
        tab = g.require(
            self.window, control_type="TabItemControl", name_re=name_re,
            timeout=timeout, what=f"editor tab matching /{name_re}/",
        )
        try:
            tab.GetSelectionItemPattern().Select()
        except Exception:
            g.click(tab, what="editor tab")

    def editor_is_open(self, name_re: str) -> bool:
        self._require_window()
        return g.find(self.window, control_type="TabItemControl", name_re=name_re) is not None

    # -- follow-up document actions (inside the open Order) ----------------- #
    def create_followup_invoice(self, order_pane, timeout: float = 15.0):
        """Click Invoice in the Order's 'Create a follow-up document' group
        (step 4.6) — NOT the top-toolbar New Invoice — so the Order link is
        preserved, then return the New Invoice pane."""
        group = g.require(
            order_pane, control_type="GroupControl",
            name="Create a follow-up document", timeout=timeout,
            what="'Create a follow-up document' group",
        )
        btn = g.require(
            group, control_type="ButtonControl", name="Invoice",
            timeout=timeout, what="follow-up Invoice button",
        )
        g.click(btn, what="follow-up Invoice")
        return self.editor_pane(PANE_NEW_INVOICE, timeout=timeout)

    # -- modal dialogs ----------------------------------------------------- #
    def wait_dialog(self, title_re: str, timeout: float = g.DEFAULT_TIMEOUT):
        """Wait for a modal (e.g. 'Select the address', 'Select a product').
        Dialogs are separate top-level windows, so search from the desktop
        root, not the app window."""
        root = auto.GetRootControl()
        dlg = g.find(root, control_type="WindowControl", name_re=title_re,
                     max_depth=3, timeout=timeout)
        if dlg is None:
            raise ControlNotFound(
                f"Dialog matching /{title_re}/ did not appear",
                user_message=f"The “{title_re}” dialog did not open.",
            )
        return dlg

    def documents_view(self, timeout: float = g.DEFAULT_TIMEOUT):
        """The bottom 'Documents' view pane, used for verification reads."""
        self._require_window()
        return g.require(
            self.window, control_type="PaneControl", name="Documents",
            timeout=timeout, what="Documents view",
        )

    # -- internal ---------------------------------------------------------- #
    def _require_window(self):
        if self.window is None or not self.window.Exists(0, 0):
            self.attach()
        return self.window
