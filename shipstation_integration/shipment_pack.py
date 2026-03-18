# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Server-side hooks for the Shipment doctype pack/SSCC workflow.

These hooks fire on Shipment submit when the Shipment has been packed
via the Shipment Delivery Note (SDN) item-level table.
"""

import frappe
from frappe import _


def before_submit(doc, method=None):
	"""
	Validate that every Shipment Delivery Note item has been assigned to a
	parcel before the Shipment is submitted.

	This validation only applies when the SDN table has item-level rows
	(i.e. any row has dn_detail or item_code populated). Pure DN-link rows
	without item details are allowed through unpacked.
	"""
	item_level_rows = [
		row for row in (doc.shipment_delivery_note or []) if row.get("item_code") or row.get("dn_detail")
	]
	if not item_level_rows:
		return

	unpacked = [row for row in item_level_rows if not row.parcel_number]
	if unpacked:
		frappe.throw(
			_(
				"All Shipment Delivery Note items must be assigned to a parcel before submitting. "
				"{0} item(s) are not yet packed."
			).format(len(unpacked))
		)


def on_submit(doc, method=None):
	"""
	After the Shipment is submitted:
	  1. Register BEAM Handling Units for each SSCC code.
	  2. Create and submit a Repack Stock Entry to record the HU
	     transformation in the stock ledger (BEAM must be installed).
	  3. Update Delivery Note item handling_unit fields to the new SSCCs.
	"""
	from shipstation_integration.beam_integration import on_shipment_submit

	on_shipment_submit(doc)
