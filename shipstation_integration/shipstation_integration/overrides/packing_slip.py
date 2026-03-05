# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from erpnext.stock.doctype.packing_slip.packing_slip import PackingSlip


class ShipstationPackingSlip(PackingSlip):
	def validate_case_nos(self):
		# Case number uniqueness is not enforced in the ShipStation workflow;
		# parcels are tracked via parcel_number on Packing Slip Items instead.
		pass

	def validate(self):
		super().validate()
		parcel_numbers = [item.parcel_number for item in (self.items or []) if item.parcel_number]
		if parcel_numbers:
			self.from_case_no = 1
			self.to_case_no = max(parcel_numbers)
