# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from erpnext.stock.doctype.packing_slip.packing_slip import PackingSlip
from shipstation_integration.cartonization import (
	cartonize_mapped_packing_slip_from_delivery_note,
	get_enabled_shipstation_cartonization_settings,
)


class ShipstationPackingSlip(PackingSlip):
	def after_mapping(self, source_doc):
		if getattr(source_doc, "doctype", None) != "Delivery Note":
			return
		cartonize_mapped_packing_slip_from_delivery_note(self)

	def validate_case_nos(self):
		# Multi-parcel cartonization bypasses overlapping package-number checks because
		# bins are tracked via parcel_number on Packing Slip Items; otherwise use ERPNext.
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
