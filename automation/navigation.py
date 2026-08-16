"""Toolbar + left-navigation actions.

Grounding comes straight from the UIA dump:
  * Top toolbar buttons are real ButtonControls with stable Names:
      'Create: New Order', 'Create: New Invoice', 'Save the current contents',
      'Create a new product', 'Create a new contact'.
  * Left-nav ('New' panel and category list) are SWT TextControls WITHOUT
    InvokePattern: 'New Contact', 'New product', 'Documents', 'VATs',
    'terms of payment'. widgets.click_link handles those via physical click.

We prefer the always-visible left-nav links over the 'Data' menu because the
menu's submenu items only materialise on expand and aren't in the tree yet.
"""
from __future__ import annotations

from . import uia, widgets
from .session import FakturamaApp


# -- top toolbar -----------------------------------------------------------
def new_order(app: FakturamaApp) -> None:
    uia.click(app.window.ButtonControl(Name="Create: New Order"), "toolbar New Order")


# -- left 'New' panel ------------------------------------------------------
def new_contact(app: FakturamaApp) -> None:
    widgets.click_link(app.window, "Create a new contact")


def new_product(app: FakturamaApp) -> None:
    widgets.click_link(app.window, "Create a new product")
