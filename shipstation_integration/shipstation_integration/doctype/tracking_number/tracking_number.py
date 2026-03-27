# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class TrackingNumber(Document):
	def validate(self):
		existing = frappe.db.get_value(
			"Tracking Number",
			{"tracking_number": self.tracking_number, "docstatus": ("!=", 2), "name": ("!=", self.name)},
			"name",
		)
		if existing:
			frappe.throw(f"Tracking Number {self.tracking_number} is already active in {existing}.")

	def on_submit(self):
		seventeentrack = frappe.get_single("Seventeen Track")
		if (
			self.amended_from
			and frappe.db.get_value(self.doctype, self.amended_from, "tracking_number")
			== self.tracking_number
		):
			seventeentrack.retrack(self.tracking_number)
		else:
			seventeentrack.track_shipment_id(self.tracking_number)
		self.db_set("subscription_status", "Active")

	def on_cancel(self):
		seventeentrack = frappe.get_single("Seventeen Track")
		seventeentrack.stop_tracking(self.tracking_number)
		self.db_set("subscription_status", "Stopped")

	def on_trash(self):
		if self.docstatus == 2:
			seventeentrack = frappe.get_single("Seventeen Track")
			seventeentrack.delete_tracking(self.tracking_number)
