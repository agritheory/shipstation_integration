# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
	get_conversion_factor,
)


class ParcelDimensions(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		bol_url: DF.Data | None
		dimension_uom: DF.Link
		height: DF.Float
		label_url: DF.Data | None
		length: DF.Float
		parcel_template: DF.Link | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		tracking_number: DF.Data | None
		tracking_url: DF.Data | None
		weight: DF.Float
		weight_uom: DF.Link
		width: DF.Float
	# end: auto-generated types

	def get_user_uoms(self):

		if hasattr(self, "_uom_cache"):

			return self._uom_cache

		user = frappe.get_cached_doc("User", frappe.session.user)

		self._uom_cache = (user.dimension_uom or "Centimeter", user.weight_uom or "Kilogram")

		return self._uom_cache

	def convert_to_dimension_uom(self, value_cm):

		if not value_cm:

			return 0

		dimension_uom, _ = self.get_user_uoms()

		factor = get_conversion_factor("Centimeter", dimension_uom)

		return value_cm * factor

	def convert_weight(self, value_kg):
		if not value_kg:
			return 0

		_, weight_uom = self.get_user_uoms()
		factor = get_conversion_factor("Kilogram", weight_uom)
		return value_kg * factor

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
		return self.convert_weight(self.weight)
