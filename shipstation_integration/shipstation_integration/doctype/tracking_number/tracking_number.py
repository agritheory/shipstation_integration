# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track import (
	get_seventeen_track_settings_for_company,
	resolve_company_from_tracking_number,
)


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
		company = resolve_company_from_tracking_number(self)
		settings = get_seventeen_track_settings_for_company(company)
		if settings and settings.seventeen_track_user:
			frappe.set_user(settings.seventeen_track_user)
		if (
			self.amended_from
			and frappe.db.get_value(self.doctype, self.amended_from, "tracking_number")
			== self.tracking_number
		):
			settings.retrack(self.tracking_number)
		else:
			settings.track_shipment_id(self.tracking_number)
		self.db_set("subscription_status", "Active")

	def on_cancel(self):
		company = resolve_company_from_tracking_number(self)
		settings = get_seventeen_track_settings_for_company(company)
		if settings and settings.seventeen_track_user:
			frappe.set_user(settings.seventeen_track_user)
		settings.stop_tracking(self.tracking_number)
		self.db_set("subscription_status", "Stopped")

	def on_trash(self):
		if self.docstatus == 2:
			company = resolve_company_from_tracking_number(self)
			settings = get_seventeen_track_settings_for_company(company)
			if settings and settings.seventeen_track_user:
				frappe.set_user(settings.seventeen_track_user)
			settings.delete_tracking(self.tracking_number)
