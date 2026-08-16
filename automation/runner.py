"""Orchestrator: drive one order dict from open through (eventually) verified
invoice, in the doc's Order-first order. Records a step log and turns any
AutomationError into a structured, user-facing result for Flask.

Currently wired end-to-end through the Order header + totals scaffolding; the
debtor/product/invoice flows run up to their first ungrounded control and stop
with a ManualReviewRequired that names exactly what's missing. That keeps every
run honest — it reports real progress and the precise next capture needed,
rather than pretending to finish.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .exceptions import AutomationError, ManualReviewRequired
from .flows import debtor as debtor_flow
from .flows import invoice as invoice_flow
from .flows import order as order_flow
from .flows import product as product_flow
from .models import ExtractedOrder
from .session import FakturamaApp


@dataclass
class StepLog:
    name: str
    ok: bool
    detail: str = ""
    elapsed: float = 0.0


@dataclass
class RunResult:
    status: str = "running"           # running | ok | manual_review | error
    steps: list[StepLog] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: Optional[dict] = None
    screenshot: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "steps": [s.__dict__ for s in self.steps],
            "warnings": self.warnings,
            "error": self.error,
            "screenshot": self.screenshot,
        }


def run_automation(record: dict, stop_after: str = "invoice") -> RunResult:
    """stop_after ∈ {'header', 'debtor', 'product', 'order', 'invoice'} lets the
    Flask UI run just part of the pipeline while we build it out."""
    result = RunResult()
    order = ExtractedOrder.from_dict(record)

    try:
        app = FakturamaApp.attach_or_launch()
        app.focus()

        _run(result, "Open New Order", lambda: order_flow.open_new_order(app))
        _run(result, "Set header (Date, Cust.Ref., price mode)", lambda: order_flow.set_header(app, order))
        
        if stop_after == "header":
            return _finish(result, app)

        _run(result, "Resolve/create Debtor", lambda: debtor_flow.resolve_or_create(app, order))
        if stop_after == "debtor":
            return _finish(result, app)

        _run(result, "Resolve/create Products + lines",
             lambda: product_flow.resolve_all(app, order))
        if stop_after == "product":
            return _finish(result, app)

        # Re-apply the header last: selecting a Debtor re-applies that
        # contact's defaults to the document and has been seen reverting the
        # Date, flipping the price mode to Gross and clearing Cust.Ref.
        _run(result, "Re-apply header before save",
             lambda: order_flow.set_header(app, order))
        _run(result, "Confirm Discount/Shipping defaults",
             lambda: order_flow.verify_order_defaults(app))
        _run(result, "Verify totals", lambda: order_flow.verify_totals(app, order))

        # save_order returns the number the editor is renamed to; every later
        # step addresses the editor by it ('New Order' stops resolving).
        saved: dict = {}
        _run(result, "Save Order",
             lambda: saved.update(no=order_flow.save_order(app)))
        _run(result, "Confirm saved Order in Documents",
             lambda: order_flow.verify_in_documents(app, order, saved["no"]))
        if stop_after == "order":
            return _finish(result, app)

        _run(result, "Open follow-up Invoice",
             lambda: order_flow.open_followup_invoice(app, saved["no"]))
        _run(result, "Complete + verify Invoice",
             lambda: invoice_flow.complete_and_verify(app, order, saved["no"]))

        return _finish(result, app)

    except ManualReviewRequired as exc:
        result.status = "manual_review"
        result.error = exc.to_dict()
    except AutomationError as exc:
        result.status = "error"
        result.error = exc.to_dict()
    except Exception as exc:  # noqa: BLE001 — never leak a raw trace to the UI
        result.status = "error"
        result.error = {"error": type(exc).__name__, "message": str(exc)}
    try:
        result.screenshot = FakturamaApp.attach().screenshot("error")
    except Exception:
        pass
    return result


def _run(result: RunResult, name: str, fn: Callable[[], object]) -> None:
    """Run one step. A step may return a list of advisory warnings — checks
    that could not be *confirmed* but are not grounds to fail the run (the
    post-save Documents read, whose OCR of that grid is unreliable). They are
    recorded for the operator instead of aborting."""
    t0 = time.time()
    out = fn()
    warnings = out if isinstance(out, list) else []
    if warnings:
        result.warnings.extend(f"{name}: {w}" for w in warnings)
        for w in warnings:
            print(f"[warn] {name}: {w}")
    result.steps.append(StepLog(
        name, True, detail="; ".join(warnings), elapsed=round(time.time() - t0, 2),
    ))


def _finish(result: RunResult, app: FakturamaApp) -> RunResult:
    if result.status == "running":
        result.status = "ok"
    return result
