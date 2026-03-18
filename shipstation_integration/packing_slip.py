# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Server-side hooks for the Packing Slip doctype.
"""

import frappe
from frappe import _


def before_submit(doc, method=None):
	"""
	Validate that every item has been assigned to a parcel before the Packing
	Slip is submitted.  SSCC generation is a deliberate manual step; parcels
	without a ``ucc128`` will receive a regular BEAM Handling Unit (UUID-derived
	name) via the Repack Stock Entry created in ``on_submit``.
	"""
	packed = [item for item in doc.items if item.parcel_number]
	if not packed:
		frappe.throw(_("All items must be assigned to a parcel before submitting a Packing Slip."))


def on_submit(doc, method=None):
	"""
	After the Packing Slip is submitted:
	  1. Register BEAM Handling Units for each SSCC code.
	  2. Create and submit a Repack Stock Entry to record the HU
	     transformation in the stock ledger (BEAM must be installed).
	  3. Update Delivery Note item ``handling_unit`` fields to the new SSCCs.
	"""
	from shipstation_integration.beam_integration import on_packing_slip_submit

	on_packing_slip_submit(doc)
