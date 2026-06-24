# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.packing_slip.packing_slip import PackingSlip
from frappe import _

from shipstation_integration.cartonization import (
	cartonize_mapped_packing_slip_from_delivery_note,
	get_enabled_shipstation_cartonization_settings,
)
from shipstation_integration.shipstation_integration.overrides.handling_unit import (
	on_packing_slip_submit,
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

	def before_submit(self):
		"""
		Validate that every item has been assigned to a parcel before the Packing
		Slip is submitted.  SSCC generation is a deliberate manual step; parcels
		without a ``ucc128`` will receive a regular BEAM Handling Unit (UUID-derived
		name) via the Repack Stock Entry created in ``on_submit``.
		"""
		packed = [item for item in self.items if item.parcel_number]
		if not packed:
			frappe.throw(_("All items must be assigned to a parcel before submitting a Packing Slip."))

	def on_submit(self):
		"""
		After the Packing Slip is submitted:
		  1. Register BEAM Handling Units for each SSCC code.
		  2. Create and submit a Repack Stock Entry to record the HU
		     transformation in the stock ledger (BEAM must be installed).
		  3. Update Delivery Note item ``handling_unit`` fields to the new SSCCs.
		"""
		on_packing_slip_submit(self)
