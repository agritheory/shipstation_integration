# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from inventory_tools.inventory_tools.overrides.packing_slip import InventoryToolsPackingSlip

from shipstation_integration.cartonization import (
	cartonize_mapped_packing_slip,
	get_enabled_shipstation_cartonization_settings,
)
from shipstation_integration.shipstation_integration.overrides.handling_unit import (
	on_packing_slip_submit,
)
from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
	apply_packing_slip_addresses_from_sales_order,
)


class ShipstationPackingSlip(InventoryToolsPackingSlip):
	def after_mapping(self, source_doc):
		apply_packing_slip_addresses_from_sales_order(self)
		cartonize_mapped_packing_slip(self)

	def validate_case_nos(self):
		if get_enabled_shipstation_cartonization_settings():
			return
		super().validate_case_nos()

	def validate(self):
		super().validate()
		if not get_enabled_shipstation_cartonization_settings():
			return
		parcel_numbers = [item.parcel_number for item in (self.items or []) if item.parcel_number]
		if parcel_numbers:
			self.from_case_no = 1
			self.to_case_no = max(parcel_numbers)

	def before_submit(self):
		if self.uses_alternative_sales_workflow_without_delivery_note():
			if not any(item.parcel_number for item in self.items or []):
				return

		packed = [item for item in self.items if item.parcel_number]
		if not packed:
			frappe.throw(_("All items must be assigned to a parcel before submitting a Packing Slip."))

	def on_submit(self):
		super().on_submit()
		on_packing_slip_submit(self)
