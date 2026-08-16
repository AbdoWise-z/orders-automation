"""Semantic widget helpers.

Thin, intention-revealing wrappers over uia.py so flow code reads like the
procedure in the design doc ("set the Cust.Ref. edit", "click the upper icon
under Addresses") instead of raw control lookups.
"""
from __future__ import annotations

from typing import Optional

import uiautomation as auto

from . import uia
from .exceptions import ControlNotFound

CT = uia.CT


def labelled_edit(scope: auto.Control, label: str) -> auto.Control:
    """The Edit whose accessible Name equals `label` (Cust.Ref., Company, Street,
    E-Mail, Telephone, Total Net, ...). These Names are stable in SWT."""
    return uia.require(scope.EditControl(Name=label), f"edit '{label}'")


def set_labelled(scope: auto.Control, label: str, value, commit: bool = True) -> None:
    uia.set_text(labelled_edit(scope, label), value, what=f"edit '{label}'",
                 commit=commit)


def read_labelled(scope: auto.Control, label: str) -> str:
    return uia.get_text(labelled_edit(scope, label))


def combo_by_name(scope: auto.Control, name: str) -> auto.Control:
    return uia.require(scope.ComboBoxControl(Name=name), f"combo '{name}'")


def upper_icon_under(scope: auto.Control, label: str) -> auto.Control:
    """Topmost icon under a label — the existing-selector for Addresses/Items."""
    return uia.images_under_label(scope, label)[0]


def lower_icon_under(scope: auto.Control, label: str) -> auto.Control:
    """Second icon under a label — the green '+' create-new control.
    Explicit helper so a flow can never confuse it with the picker."""
    imgs = uia.images_under_label(scope, label)
    if len(imgs) < 2:
        raise ControlNotFound(f"second (create) icon under '{label}'")
    return imgs[1]


def click_link(scope: auto.Control, name: str) -> None:
    """Click an SWT navigation text-link (New Contact, New product, Documents,
    VATs, terms of payment). These are TextControls without InvokePattern, so
    uia.click falls through to DoDefaultAction / physical click."""
    uia.click(scope.Control(Name=name), f"link '{name}'")


def toolbar_button(scope: auto.Control, name: str) -> auto.Control:
    return uia.require(scope.ButtonControl(Name=name), f"toolbar button '{name}'")
