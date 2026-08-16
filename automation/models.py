"""Typed, read-only view over the extractor's JSON (the shape produced by
``extraction`` and saved by ``app.py``). Keeping this separate from the raw dict
means the flows read ``order.debtor.company`` instead of poking string keys, and
gives one place to attach computed values (gross price, expected line totals).

This is intentionally a *view*, not a validator. Business validation
(exact-match rules, totals reconciliation) lives in ``matching.py`` /
``validation.py`` so the model stays dumb and stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from . import normalization as norm


def _s(v: Any) -> Optional[str]:
    if v in (None, ""):
        return None
    return str(v)


def _num(v: Any) -> Optional[Decimal]:
    if v in (None, ""):
        return None
    return Decimal(str(v))


@dataclass(frozen=True)
class Address:
    street: Optional[str] = None
    zip: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Address":
        d = d or {}
        return cls(_s(d.get("street")), _s(d.get("zip")), _s(d.get("city")), _s(d.get("country")))

    def is_empty(self) -> bool:
        return not any((self.street, self.zip, self.city, self.country))


@dataclass(frozen=True)
class Debtor:
    company: Optional[str] = None
    contact_name: Optional[str] = None
    alias: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    payment_method: Optional[str] = None
    paid_status: Optional[str] = None
    payment_date: Optional[str] = None
    billing_address: Address = field(default_factory=Address)
    delivery_address: Address = field(default_factory=Address)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Debtor":
        d = d or {}
        return cls(
            company=_s(d.get("company")),
            contact_name=_s(d.get("contact_name")),
            alias=_s(d.get("alias")),
            email=_s(d.get("email")),
            phone=_s(d.get("phone")),
            payment_method=_s(d.get("payment_method")),
            paid_status=_s(d.get("paid_status")),
            payment_date=_s(d.get("payment_date")),
            billing_address=Address.from_dict(d.get("billing_address")),
            delivery_address=Address.from_dict(d.get("delivery_address")),
        )

    @property
    def first_name(self) -> Optional[str]:
        return norm.split_name(self.contact_name)[0]

    @property
    def last_name(self) -> Optional[str]:
        return norm.split_name(self.contact_name)[1]

    @property
    def is_paid(self) -> bool:
        return (self.paid_status or "").strip().upper() == "PAID"

    @property
    def delivery_same_as_billing(self) -> bool:
        """True when billing and delivery are identical (step 2.8: reuse the
        Main address for both roles instead of creating a second address)."""
        if self.delivery_address.is_empty():
            return True
        return self.delivery_address == self.billing_address


@dataclass(frozen=True)
class LineItem:
    sku: Optional[str] = None
    description: Optional[str] = None
    quantity: Optional[Decimal] = None
    unit: Optional[str] = None
    unit_net_price: Optional[Decimal] = None
    discount_pct: Optional[Decimal] = None
    vat_pct: Optional[Decimal] = None
    line_net_total: Optional[Decimal] = None

    @classmethod
    def from_dict(cls, d: dict) -> "LineItem":
        return cls(
            sku=_s(d.get("sku")),
            description=_s(d.get("description")),
            quantity=_num(d.get("quantity")),
            unit=_s(d.get("unit")),
            unit_net_price=_num(d.get("unit_net_price")),
            discount_pct=_num(d.get("discount_pct")) or Decimal(0),
            vat_pct=_num(d.get("vat_pct")),
            line_net_total=_num(d.get("line_net_total")),
        )

    @property
    def gross_price(self) -> Decimal:
        """Product-master gross price to type into the New product editor."""
        return norm.gross_from_net(self.unit_net_price, self.vat_pct)

    @property
    def expected_line_total(self) -> Decimal:
        return norm.line_net_total(self.quantity, self.unit_net_price, self.discount_pct)

    @property
    def vat_name(self) -> str:
        """Fakturama VAT record name to look up/create (step 3.6), e.g. 'VAT 19%'."""
        return norm.vat_name(self.vat_pct)

    def line_total_matches_source(self) -> bool:
        """Cross-check the computed line total against the extracted source
        total, if the source supplied one."""
        if self.line_net_total is None:
            return True
        return self.expected_line_total == norm.round2(self.line_net_total)


@dataclass(frozen=True)
class OrderHeader:
    order_date: Optional[str] = None            # ISO from extractor
    external_reference: Optional[str] = None
    customer_id: Optional[str] = None
    currency: Optional[str] = None

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "OrderHeader":
        d = d or {}
        return cls(
            order_date=_s(d.get("order_date")),
            external_reference=_s(d.get("external_reference")),
            customer_id=_s(d.get("customer_id")),
            currency=_s(d.get("currency")),
        )

    @property
    def fakturama_date(self) -> Optional[str]:
        return norm.to_fakturama_date(self.order_date)


@dataclass(frozen=True)
class ExtractedOrder:
    header: OrderHeader
    debtor: Debtor
    items: list[LineItem]
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, record: dict) -> "ExtractedOrder":
        return cls(
            header=OrderHeader.from_dict(record.get("order")),
            debtor=Debtor.from_dict(record.get("debtor")),
            items=[LineItem.from_dict(it) for it in (record.get("items") or [])],
            meta=dict(record.get("_meta") or {}),
        )

    @property
    def order_id(self) -> Optional[str]:
        return self.meta.get("order_id")

    @property
    def expected_total_net(self) -> Decimal:
        return norm.round2(sum((it.expected_line_total for it in self.items), Decimal(0)))

    @property
    def expected_total_vat(self) -> Decimal:
        """VAT summed per line (matching how Fakturama totals a document)
        rather than applied once to the net total, so mixed VAT rates and
        per-line rounding agree with the UI."""
        return norm.round2(sum(
            (norm.round2(it.expected_line_total * (it.vat_pct or Decimal(0)) / Decimal(100))
             for it in self.items),
            Decimal(0),
        ))

    @property
    def expected_total_gross(self) -> Decimal:
        return norm.round2(self.expected_total_net + self.expected_total_vat)