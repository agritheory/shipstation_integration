# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Incoterm resolution and carrier billing derivation.

Incoterms are the business source of truth on Sales Order, Delivery Note, and
Shipment. Carrier APIs still need separate ``billing_type`` and ``payment_terms``
fields — those are derived here at the integration boundary, not stored as
equivalent enums.
"""

from __future__ import annotations

import frappe

# Incoterms where the buyer arranges and pays main carriage from the seller's
# handover point. Parcel labels should bill the customer's carrier account when
# one is on file.
CUSTOMER_CARRIAGE_INCOTERMS = frozenset({"EXW", "FCA"})

# Derived carrier API values keyed by Incoterm code (ICC 2020).
INCOTERM_CARRIER_BILLING: dict[str, dict[str, str]] = {
	"EXW": {"billing_type": "Consignee", "payment_terms": "Collect"},
	"FCA": {"billing_type": "Consignee", "payment_terms": "Collect"},
}

DEFAULT_CARRIER_BILLING = {"billing_type": "Shipper", "payment_terms": "Prepaid"}


def normalize_incoterm_code(incoterm: str | None) -> str | None:
	"""Return the three-letter Incoterm code from a Link value or label."""
	if not incoterm:
		return None
	token = str(incoterm).strip().split()[0].upper()
	return token or None


def incoterm_from_doc(doctype: str, docname: str) -> str | None:
	if not frappe.get_meta(doctype).has_field("incoterm"):
		return None
	return frappe.db.get_value(doctype, docname, "incoterm")


def get_first_sales_order_from_shipment(doc) -> str | None:
	for row in doc.get("shipment_delivery_note") or []:
		if row.get("against_sales_order"):
			return row.against_sales_order
		if row.get("delivery_note"):
			so_name = frappe.db.get_value(
				"Delivery Note Item",
				{"parent": row.delivery_note, "against_sales_order": ("is", "set")},
				"against_sales_order",
			)
			if so_name:
				return so_name
	return None


def resolve_incoterm_for_shipment(doc) -> str | None:
	if doc.get("incoterm"):
		return doc.incoterm

	delivery_note = next(
		(
			row.delivery_note
			for row in (doc.get("shipment_delivery_note") or [])
			if row.get("delivery_note")
		),
		None,
	)
	if delivery_note:
		incoterm = incoterm_from_doc("Delivery Note", delivery_note)
		if incoterm:
			return incoterm

	so_name = get_first_sales_order_from_shipment(doc)
	if so_name:
		return incoterm_from_doc("Sales Order", so_name)
	return None


def populate_shipment_incoterm(doc) -> None:
	"""Copy incoterm from a linked Delivery Note or Sales Order when blank."""
	if doc.get("incoterm"):
		return
	incoterm = resolve_incoterm_for_shipment(doc)
	if incoterm:
		doc.incoterm = incoterm


def resolve_incoterm_for_source(source) -> str | None:
	"""Incoterm for parcel label billing from a packing-slip-shaped source."""
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_first_sales_order_from_packing_slip,
	)

	if source.get("incoterm"):
		return source.incoterm
	if source.get("delivery_note"):
		incoterm = incoterm_from_doc("Delivery Note", source.delivery_note)
		if incoterm:
			return incoterm

	so_name = get_first_sales_order_from_packing_slip(source)
	if so_name:
		return incoterm_from_doc("Sales Order", so_name)
	return None


def incoterm_requires_customer_shipping_account(incoterm: str | None) -> bool:
	code = normalize_incoterm_code(incoterm)
	return code in CUSTOMER_CARRIAGE_INCOTERMS


def carrier_billing_from_incoterm(incoterm: str | None) -> dict[str, str]:
	code = normalize_incoterm_code(incoterm)
	if not code:
		return dict(DEFAULT_CARRIER_BILLING)
	return dict(INCOTERM_CARRIER_BILLING.get(code, DEFAULT_CARRIER_BILLING))


def resolve_carrier_billing(doc) -> dict[str, str]:
	"""Carrier billing fields for LTL APIs, preferring incoterm when set."""
	incoterm = doc.get("incoterm") or resolve_incoterm_for_shipment(doc)
	if incoterm:
		return carrier_billing_from_incoterm(incoterm)
	return {
		"billing_type": doc.get("billing_type") or DEFAULT_CARRIER_BILLING["billing_type"],
		"payment_terms": doc.get("payment_terms") or DEFAULT_CARRIER_BILLING["payment_terms"],
	}
