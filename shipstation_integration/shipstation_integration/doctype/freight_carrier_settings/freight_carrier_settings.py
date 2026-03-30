# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class FreightCarrierSettings(Document):
	def validate(self):
		if not self.company or not self.supplier or self.disabled:
			return
		existing = frappe.db.exists(
			"Freight Carrier Settings",
			{"company": self.company, "supplier": self.supplier, "disabled": 0},
		)
		if existing and existing != self.name:
			frappe.throw(
				_("Freight Carrier Settings already exists for Company {0} and Supplier {1}.").format(
					self.company, self.supplier
				)
			)


def get_freight_carrier_settings(
	company: str | None, supplier: str | None
) -> FreightCarrierSettings | None:
	"""Return enabled Freight Carrier Settings for the given company and supplier, if any."""
	if not company or not supplier:
		return None
	name = frappe.db.get_value(
		"Freight Carrier Settings",
		filters={"company": company, "supplier": supplier, "disabled": 0},
		fieldname="name",
	)
	if not name:
		return None
	return frappe.get_doc("Freight Carrier Settings", name)


def sync_ltl_api_credentials_from_shipstation_settings(ss) -> None:
	"""
	Copy ShipStation API v2 key and (if blank) base URL to Freight Carrier Settings for every
	Company × transporter Supplier so LTL calls use Freight Carrier Settings only.
	"""
	if not getattr(ss, "enable_shipstation_api", False):
		return
	api_key = ss.get_password("shipstation_api_key")
	if not api_key:
		return
	ss_base = (getattr(ss, "base_url", None) or "https://api.shipengine.com").strip().rstrip("/")

	for company in frappe.get_all("Company", pluck="name"):
		for supplier in frappe.get_all("Supplier", filters={"is_transporter": 1}, pluck="name"):
			existing = frappe.db.get_value(
				"Freight Carrier Settings",
				{"company": company, "supplier": supplier},
				"name",
			)
			if existing:
				fc = frappe.get_doc("Freight Carrier Settings", existing)
				if fc.disabled:
					continue
			else:
				fc = frappe.new_doc("Freight Carrier Settings")
				fc.company = company
				fc.supplier = supplier
				fc.insert(ignore_permissions=True)
				fc.reload()

			# Only sync the ShipEngine API key to FCS records that use ShipEngine/ShipStation.
			# Records with a non-ShipEngine base_url (e.g. WWEX, Banyan, ODFL direct) manage their
			# own credentials and must not have them overwritten by the ShipStation API key.
			existing_base = (fc.base_url or "").strip().lower()
			is_shipengine_provider = not existing_base or any(
				d in existing_base for d in ["shipengine.com", "shipstation.com"]
			)
			if is_shipengine_provider:
				fc.set("ltl_api_key", api_key)
				if not existing_base:
					fc.base_url = ss_base
			fc.save(ignore_permissions=True)
