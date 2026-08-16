"""Exception hierarchy for the Fakturama automation.

Design goals
------------
* Every failure carries a short, human-readable ``user_message`` the Flask layer
  can show directly to the operator so they can fix things and retry.
* ``ManualReviewRequired`` is the "stop for manual review" case from the design
  doc. It is a *controlled* stop, not a crash: the automation did its job and
  correctly decided a human must decide. The flow leaves Fakturama in a
  consistent, inspectable state before raising it.
* ``context`` is a plain, JSON-serialisable dict so it can be written onto the
  order record and rendered on the status page.
"""

from __future__ import annotations

from typing import Any, Optional


class AutomationError(Exception):
    """Base class for anything that goes wrong while driving Fakturama."""

    default_user_message = "The automation hit an unexpected problem."

    def __init__(
        self,
        message: str,
        *,
        user_message: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
        step: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or self.default_user_message
        self.context = context or {}
        self.step = step

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "message": str(self),
            "user_message": self.user_message,
            "step": self.step,
            "context": self.context,
        }


class FakturamaNotRunning(AutomationError):
    default_user_message = (
        "Fakturama window was not found. Start Fakturama, open a company, and "
        "make sure the main window is visible, then retry."
    )


class ControlNotFound(AutomationError):
    """A UI element we needed could not be located in the accessibility tree."""

    default_user_message = (
        "A screen element could not be found. The Fakturama layout may have "
        "changed, or a previous step left the app on the wrong screen."
    )


class AmbiguousMatch(AutomationError):
    """More than one candidate matched where exactly one was expected."""

    default_user_message = "Several matching elements were found where one was expected."


class ValueNotApplied(AutomationError):
    """We wrote a field but it did not read back the value we set."""

    default_user_message = (
        "A value could not be written into a field and verified. The field may be "
        "read-only, disabled, or reformatting the input."
    )


class VerificationFailed(AutomationError):
    """A post-condition (totals, saved row, copied fields) did not hold."""

    default_user_message = "A saved record did not match what was expected."


class ManualReviewRequired(AutomationError):
    """A deliberate, safe stop that requires a human decision.

    Raised for ambiguous/conflicting master-data matches, a missing payment
    method on the invoice, a product that will not appear after creation, etc.
    """

    default_user_message = "This order needs manual review before it can be completed."

    def __init__(
        self,
        reason: str,
        *,
        context: Optional[dict[str, Any]] = None,
        step: Optional[str] = None,
    ) -> None:
        super().__init__(reason, user_message=reason, context=context, step=step)
        self.reason = reason
