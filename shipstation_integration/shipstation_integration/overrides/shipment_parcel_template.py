import frappe
from erpnext.stock.doctype.shipment_parcel_template.shipment_parcel_template import (
	ShipmentParcelTemplate,
)
from frappe.model.document import Document


class ShipstationShipmentParcelTemplate(ShipmentParcelTemplate):
	def get_user_uoms(self):
		user = frappe.get_cached_doc("User", frappe.session.user)
		return (user.length_uom or "Centimeter", user.weight_uom or "Kilogram")

	def convert_length(self, value_cm):
		length_uom, _ = self.get_user_uoms()
		factor = get_conversion_factor("Centimeter", length_uom)
		return value_cm * factor if value_cm else 0

	def convert_weight(self, value_kg):
		_, weight_uom = self.get_user_uoms()
		factor = get_conversion_factor("Kg", weight_uom)
		return value_kg * factor if value_kg else 0

	@property
	def length_display(self):
		return self.convert_length(self.length)

	@property
	def width_display(self):
		return self.convert_length(self.width)

	@property
	def height_display(self):
		return self.convert_length(self.height)

	@property
	def weight_display(self):
		return self.convert_weight(self.weight)


def get_conversion_factor(from_uom, to_uom):
	if from_uom == to_uom:
		return 1

	conv = frappe.db.get_value(
		"UOM Conversion Factor", {"to_uom": to_uom, "from_uom": from_uom}, "value"
	)
	if conv:
		return conv

	conv = frappe.db.get_value(
		"UOM Conversion Factor", {"to_uom": from_uom, "from_uom": to_uom}, "value"
	)
	if conv:
		return 1 / conv

	frappe.throw(f"No conversion from {from_uom} to {to_uom}")
