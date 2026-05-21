# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import json

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


@frappe.whitelist()
def get_tracking_number_map_data(filters=None):
	parsed = json.loads(filters) if isinstance(filters, str) else (filters or [])
	frappe_filters = [["docstatus", "=", 1], ["last_latitude", "!=", ""]] + parsed

	tns = frappe.get_all(
		"Tracking Number",
		filters=frappe_filters,
		fields=[
			"name",
			"tracking_number",
			"seventeen_track_status",
			"last_latitude",
			"last_longitude",
			"last_event_location",
		],
	)

	return {
		"type": "FeatureCollection",
		"features": [
			{
				"type": "Feature",
				"properties": {
					"name": tn.name,
					"tracking_number": tn.tracking_number,
					"status": tn.seventeen_track_status or "",
					"location": tn.last_event_location or "",
				},
				"geometry": {
					"type": "Point",
					"coordinates": [float(tn.last_longitude), float(tn.last_latitude)],
				},
			}
			for tn in tns
			if tn.last_latitude and tn.last_longitude
		],
	}
