# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import add_days


class ShipmentQuotation(Document):
	def autoname(self):
		prefix = f"SQ-{self.shipment}-{self.carrier_scac}-"
		self.name = prefix + getseries(prefix, 2)

	def validate(self):
		self.calculate_estimated_delivery_date()
		self.validate_accept_quote()

	def calculate_estimated_delivery_date(self):
		if self.pickup_date and self.estimated_delivery_days and not self.estimated_delivery_date:
			self.estimated_delivery_date = add_days(self.pickup_date, self.estimated_delivery_days)

	def validate_accept_quote(self):
		prev_doc = self.get_doc_before_save()

		if not self.accept_quote and (prev_doc and prev_doc.accept_quote):
			# User un-checked accept_quote - reset Shipment fields
			self.set_or_reset_shipment_quote_fields(reset_fields=True)
		elif self.accept_quote and (prev_doc and prev_doc.accept_quote):
			# User already accepted quote, do nothing
			return
		elif self.accept_quote:
			# User accepted quote - validate no other accepted quote, set Shipment fields
			other = frappe.get_all(
				"Shipment Quotation", {"shipment": self.shipment, "accept_quote": 1, "name": ["!=", self.name]}
			)
			if other:
				frappe.throw(
					msg=_(
						f"Shipment Quotation {other[0].name} is already accepted for Shipment {self.shipment} - there can only be one accepted quote per Shipment. You must un-check 'Accept Quote' on that quote before you can accept this one."
					),
					title="Shipment Already has an Accepted Quotation",
				)
			else:
				self.set_or_reset_shipment_quote_fields()

	def set_or_reset_shipment_quote_fields(self, reset_fields: bool = False) -> None:
		dt, dn = "Shipment", self.shipment
		quote_fields = ["quote_or_offer_id", "quote_or_offer_transaction_id", "estimated_delivery_date"]
		for field in quote_fields:
			val = None if reset_fields else self.get(field)
			frappe.set_value(dt, dn, field, val)

		aq_val = None if reset_fields else self.name
		frappe.set_value(dt, dn, "accepted_quotation", aq_val)


@frappe.whitelist()
def check_if_shipment_pickup_scheduled(doc: ShipmentQuotation | str) -> dict:
	"""
	Checks if the Shipment in shipment field has a pickup scheduled by checking values set in
	its pickup_id or awb_number (BOL/tracking/PRO number) fields.

	Args:
	doc: a Shipment Quotation doc

	Returns:
	dict with "pickup_scheduled" key set to a boolean value
	"""
	doc = frappe._dict(json.loads(doc)) if isinstance(doc, str) else doc
	dt, dn = "Shipment", doc.shipment
	pu_scheduled = frappe.get_value(dt, dn, "pickup_id") or frappe.get_value(dt, dn, "awb_number")
	return {"pickup_scheduled": bool(pu_scheduled)}
