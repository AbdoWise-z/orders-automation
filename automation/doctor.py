"""Grounding self-check.

Run this FIRST on the machine with Fakturama open. It attaches to the window and
verifies that every control the flows depend on can actually be found on THIS
launch (remember: AutomationIds change every launch, so this proves our
Name/structure-based grounding holds). It never modifies anything.

    python -m automation.doctor

It's also exposed to Flask (POST /automation/doctor) so the report renders in
the browser.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import uia
from .exceptions import AutomationError
from .session import FakturamaApp


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    running: bool = False
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.running and all(c.ok for c in self.checks)

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "ok": self.ok,
            "checks": [c.__dict__ for c in self.checks],
        }


def _check(report: Report, name: str, fn: Callable[[], str]) -> None:
    try:
        detail = fn() or "found"
        report.checks.append(Check(name, True, detail))
    except Exception as exc:  # noqa: BLE001 — a failed probe is a reportable result
        report.checks.append(Check(name, False, str(exc)))


def run_doctor() -> Report:
    report = Report()
    try:
        app = FakturamaApp.attach()
    except AutomationError as exc:
        report.checks.append(Check("attach", False, str(exc)))
        return report
    report.running = True
    app.focus()
    win = app.window

    _check(report, "toolbar: Create: New Order",
           lambda: _need(win.ButtonControl(Name="Create: New Order")))
    _check(report, "toolbar: Save the current contents",
           lambda: _need(win.ButtonControl(Name="Save the current contents")))
    _check(report, "toolbar: Create: New Invoice",
           lambda: _need(win.ButtonControl(Name="Create: New Invoice")))
    _check(report, "left-nav: New Contact",
           lambda: _need(win.TextControl(Name="New Contact")))
    _check(report, "left-nav: New product",
           lambda: _need(win.TextControl(Name="New product")))
    _check(report, "left-nav: VATs",
           lambda: _need(win.TextControl(Name="VATs")))
    _check(report, "left-nav: terms of payment",
           lambda: _need(win.TextControl(Name="terms of payment")))
    _check(report, "left-nav: Documents",
           lambda: _need(win.TextControl(Name="Documents")))

    # These require a New Order editor to be open; report them as informational.
    _check(report, "editor: New Order pane (open one to test)",
           lambda: _need(win.PaneControl(Name="New Order")))
    return report


def _need(ctrl) -> str:
    if not uia.exists(ctrl, 3):
        raise AutomationError("not found on this launch")
    r = ctrl.BoundingRectangle
    return f"at ({r.left},{r.top})"


if __name__ == "__main__":
    rep = run_doctor()
    print(f"Fakturama running: {rep.running}   overall ok: {rep.ok}\n")
    for c in rep.checks:
        print(f"  [{'OK ' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
