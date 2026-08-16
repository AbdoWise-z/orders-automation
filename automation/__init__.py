"""Fakturama image-to-cash automation package.

Foundation modules (this increment):
    exceptions       - error hierarchy incl. ManualReviewRequired
    normalization    - date/money formatting and order math (no UI deps)
    models           - typed view over the extracted order JSON
    grounding        - the only module that touches uiautomation
    app_session      - top-window ownership, tabs, toolbar, nav, dialogs

Flow modules (next increment): matching, selectors, order_flow, debtor_flow,
product_flow, invoice_flow, runner.
"""

from . import exceptions, models, normalization  # noqa: F401

__all__ = ["exceptions", "models", "normalization"]
