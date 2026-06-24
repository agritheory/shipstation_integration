# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import httpx
from erpnext.stock.doctype.shipment_parcel_template.shipment_parcel_template import (
	ShipmentParcelTemplate,
)
from frappe import _

from shipstation_integration.parcel_uom_conversion import parcel_uom_factor
from shipstation_integration.utils import get_shipstation_settings


class ShipstationShipmentParcelTemplate(ShipmentParcelTemplate):
	def after_insert(self):
		self.sync_physical_dimension()

	def on_update(self):
		self.sync_physical_dimension()

	def sync_physical_dimension(self):
		if "inventory_tools" not in frappe.get_installed_apps():
			return

		settings_name = frappe.db.get_value(
			"Shipstation Settings",
			{"enabled": 1, "create_physical_dimension_per_parcel_template": 1},
			"name",
		)
		if not settings_name:
			return

		existing = frappe.db.get_value(
			"Physical Dimension",
			{
				"reference_doctype": "Shipment Parcel Template",
				"reference_document": self.name,
				"dimension_type": "Interior",
			},
			"name",
		)

		if existing:
			pd = frappe.get_doc("Physical Dimension", existing)
			pd.uom = "Centimeter"
			pd.item_length = self.length
			pd.item_width = self.width
			pd.item_height = self.height
			pd.item_weight = self.weight
			pd.save(ignore_permissions=True)
			return False
		else:
			pd = frappe.new_doc("Physical Dimension")
			pd.reference_doctype = "Shipment Parcel Template"
			pd.reference_document = self.name
			pd.dimension_type = "Interior"
			pd.uom = "Centimeter"
			pd.item_length = self.length
			pd.item_width = self.width
			pd.item_height = self.height
			pd.item_weight = self.weight
			pd.insert(ignore_permissions=True)
			return True

	def get_user_uoms(self):
		user = frappe.get_cached_doc("User", frappe.session.user)
		return (user.dimension_uom or "Centimeter", user.weight_uom or "Kg")

	def convert_to_dimension_uom(self, value_cm):
		dimension_uom = self.get_user_uoms()[0]
		factor = parcel_uom_factor("Centimeter", dimension_uom)
		return value_cm * factor if value_cm else 0

	def convert_to_weight_uom(self, value_kg):
		weight_uom = self.get_user_uoms()[1]
		factor = parcel_uom_factor("Kg", weight_uom)
		return value_kg * factor if value_kg else 0

	@property
	def length_display(self):
		return self.convert_to_dimension_uom(self.length)

	@property
	def width_display(self):
		return self.convert_to_dimension_uom(self.width)

	@property
	def height_display(self):
		return self.convert_to_dimension_uom(self.height)

	@property
	def weight_display(self):
		return self.convert_to_weight_uom(self.weight)


@frappe.whitelist()
def sync_parcel_template(template_name: str):
	doc = frappe.get_doc("Shipment Parcel Template", template_name)

	if doc.get("skip_shipstation_sync"):
		frappe.throw(_("This parcel template is marked to skip ShipStation sync"))

	if not doc.length or not doc.width or not doc.height:
		frappe.throw(_("Package dimensions are required"))

	if not doc.package_code:
		frappe.throw(_("Package Code is required"))

	settings = get_shipstation_settings(None)
	api_key = settings.get_password("shipstation_api_key")

	package_code = doc.package_code
	if not package_code.startswith("custom_"):
		package_code = f"custom_{package_code}"

	payload = {
		"package_code": package_code,
		"name": doc.parcel_template_name,
		"dimensions": {
			"unit": "centimeter",
			"length": doc.length,
			"width": doc.width,
			"height": doc.height,
		},
	}

	try:
		with httpx.Client(timeout=30) as client:
			response = client.post(
				"https://api.shipstation.com/v2/packages",
				headers={
					"API-Key": api_key,
					"Content-Type": "application/json",
				},
				json=payload,
			)

		if response.status_code not in (200, 201):
			frappe.throw(
				_("ShipStation error ({0}): {1}").format(
					response.status_code,
					response.text,
				)
			)

		data = response.json()

		doc.db_set(
			{
				"package_id": data.get("package_id"),
				"package_code": data.get("package_code"),
				"is_package_synced": 1,
			}
		)

		frappe.msgprint(_("Package synced successfully with ShipStation"))

	except Exception as e:
		frappe.log_error(
			title="ShipStation Package Sync Failed",
			message=frappe.get_traceback(),
		)
		raise
