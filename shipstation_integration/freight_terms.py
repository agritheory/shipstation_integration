# Copyright (c) 2026, Ultra PRO International LLC and contributors
# For license information, please see license.txt

"""One place where the four freight terms are spelled.

Ultra PRO sells freight on the Sales Order as "Prepaid & Add" and "3rd Party
Billing". ERPNext's Shipment doctype spells the same two terms "Prepaid and Add"
and "Third Party", and splits the idea again into a separate billing_type.
Nothing translated between them, and nothing carried the order's terms forward at
all, so every freight booking read a blank field and fell back to Prepaid. An
order sold Collect was booked Prepaid and no one was told.

Ship Via had the same shape of problem. It existed on the Sales Order, the Pick
List and the Packing Slip, but not on the Delivery Note or the Shipment, which
are the two documents a label is actually bought from.
"""

import frappe

# Keyed by the Sales Order and Delivery Note vocabulary, which is the one the
# business uses. billing_type is what the freight providers read to decide who
# the bill goes to, payment_terms is how it gets paid, and both have to be set
# or the provider quietly assumes Prepaid.
FREIGHT_TERMS = {
	"Prepaid": {"payment_terms": "Prepaid", "billing_type": "Shipper"},
	"Prepaid & Add": {"payment_terms": "Prepaid and Add", "billing_type": "Shipper"},
	"Collect": {"payment_terms": "Collect", "billing_type": "Consignee"},
	"3rd Party Billing": {"payment_terms": "Third Party", "billing_type": "Third Party"},
}

ORDER_TERMS = {terms["payment_terms"]: term for term, terms in FREIGHT_TERMS.items()}

TERMS_FIELDS = ("up_ship_via", "up_freight_term")


def to_payment_terms(freight_term: str | None) -> str:
	"""The Shipment doctype's spelling of an order's freight term."""
	return FREIGHT_TERMS.get(freight_term or "", {}).get("payment_terms", "")


def to_billing_type(freight_term: str | None) -> str:
	"""Who the freight bill goes to, in the Shipment doctype's spelling."""
	return FREIGHT_TERMS.get(freight_term or "", {}).get("billing_type", "")


def to_freight_term(payment_terms: str | None) -> str:
	"""An order's freight term, read back off a Shipment."""
	return ORDER_TERMS.get(payment_terms or "", "")


def present_fields(doctype: str) -> list:
	"""Which of the two terms fields this doctype actually has.

	They are custom fields, so a bench that has not run install_fields yet is a
	real state and asking the database for a column it does not have is a hard
	error on every save.
	"""
	meta = frappe.get_meta(doctype)
	return [fieldname for fieldname in TERMS_FIELDS if meta.has_field(fieldname)]


def resolve_shipping_terms(delivery_note: str | None) -> tuple:
	"""Ship Via and freight term for a Delivery Note, falling back to its order.

	The Delivery Note holds the freight term but not Ship Via, which is read from
	the order behind it. See install_fields for why. The freight bill does not
	care which document we learned it from.
	"""
	if not delivery_note:
		return "", ""

	fields = present_fields("Delivery Note")
	note = (fields and frappe.db.get_value("Delivery Note", delivery_note, fields, as_dict=True)) or {}
	ship_via = note.get("up_ship_via") or ""
	freight_term = note.get("up_freight_term") or ""
	if ship_via and freight_term:
		return ship_via, freight_term

	sales_order = frappe.db.get_value(
		"Delivery Note Item",
		{"parent": delivery_note, "against_sales_order": ("is", "set")},
		"against_sales_order",
	)
	if sales_order:
		sold = frappe.db.get_value("Sales Order", sales_order, TERMS_FIELDS, as_dict=True) or {}
		ship_via = ship_via or sold.get("up_ship_via") or ""
		freight_term = freight_term or sold.get("up_freight_term") or ""

	return ship_via, freight_term


def set_delivery_note_shipping_terms(doc, method=None):
	"""Carry the freight term from the order onto the Delivery Note.

	The warehouse often skips the packing slip and buys the label straight from
	the note, and without this the note is buying blind: no record of who pays for
	the freight. Only the fields the Delivery Note actually has are copied, so
	this quietly does less on a bench where Ship Via is not one of them.
	"""
	fields = present_fields(doc.doctype)
	if not fields or all(doc.get(fieldname) for fieldname in fields):
		return

	sales_order = next(
		(item.against_sales_order for item in (doc.get("items") or []) if item.get("against_sales_order")),
		None,
	)
	if not sales_order:
		return

	sold = frappe.db.get_value("Sales Order", sales_order, TERMS_FIELDS, as_dict=True) or {}
	for fieldname in fields:
		if not doc.get(fieldname) and sold.get(fieldname):
			doc.set(fieldname, sold.get(fieldname))


def set_shipment_shipping_terms(doc, method=None):
	"""Translate the order's freight terms onto the Shipment before it is booked.

	Every freight provider reads payment_terms or billing_type and defaults to
	Prepaid when they are blank, which they always were, because nothing filled
	them in. This is the only place the two vocabularies meet.
	"""
	delivery_note = next(
		(row.delivery_note for row in (doc.get("shipment_delivery_note") or []) if row.get("delivery_note")),
		None,
	)
	if not delivery_note:
		return

	ship_via, freight_term = resolve_shipping_terms(delivery_note)

	if ship_via and doc.meta.has_field("up_ship_via") and not doc.get("up_ship_via"):
		doc.up_ship_via = ship_via

	if not freight_term:
		return
	if not doc.get("payment_terms"):
		doc.payment_terms = to_payment_terms(freight_term)
	if not doc.get("billing_type"):
		doc.billing_type = to_billing_type(freight_term)


def install_fields():
	"""Add Ship Via to the Shipment.

	Options are copied from the Sales Order rather than written out again, so the
	lists cannot drift apart the way the freight terms did.

	The Delivery Note deliberately does not get one. Its table is within a few
	hundred bytes of the InnoDB row limit, so adding a column there fails outright,
	and Ship Via belongs to the order anyway: storing a second copy on the note is
	how the two come to disagree. resolve_shipping_terms reads it from the order.
	"""
	options = frappe.get_meta("Sales Order").get_field("up_ship_via").options
	created = []
	for doctype, insert_after in (("Shipment", "payment_terms"),):
		if frappe.db.exists("Custom Field", f"{doctype}-up_ship_via"):
			continue
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": doctype,
				"fieldname": "up_ship_via",
				"label": "Ship Via",
				"fieldtype": "Select",
				"options": options,
				"insert_after": insert_after,
				"allow_on_submit": 1,
			}
		).insert(ignore_permissions=True)
		created.append(doctype)

	frappe.db.commit()
	return created
