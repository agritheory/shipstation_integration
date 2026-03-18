# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Migration patch: populate item-level fields on existing Shipment Delivery Note rows.

Before this patch, Shipment Delivery Note rows only stored a ``delivery_note``
link and a ``grand_total``.  After adding custom fields (dn_detail, item_code,
item_name, qty, stock_uom) the rows are still valid but empty at the item level.

This patch attempts to auto-populate each bare SDN row by matching it to the
corresponding Delivery Note items.  When a Shipment has exactly one DN row that
links to a DN with exactly one line item, the DN item details are written back
automatically.

For Shipments with multiple DN rows or DNs with multiple items, the rows are
left as-is (bare DN links) because there is no unambiguous way to determine
which DN line maps to which SDN row.  Users may use the "Fetch DN Items" button
in the Shipment form to re-populate those rows manually.

This patch is a best-effort, data-safe migration: it never deletes or overwrites
existing values, and it never fails the migration if a DN is not found.
"""

import frappe
from frappe import _


def execute():
	shipments_with_sdn = frappe.get_all(
		"Shipment",
		filters={"docstatus": ("in", [0, 1])},
		fields=["name"],
	)

	for row in shipments_with_sdn:
		try:
			migrate_shipment(row.name)
		except Exception:
			frappe.log_error(
				title=f"SDN migration failed for Shipment {row.name}",
				message=frappe.get_traceback(),
			)


def migrate_shipment(shipment_name: str) -> None:
	"""
	Attempt to populate item-level fields for bare SDN rows on a single Shipment.
	"""
	sdn_rows = frappe.get_all(
		"Shipment Delivery Note",
		filters={"parent": shipment_name, "dn_detail": ("is", "not set")},
		fields=["name", "delivery_note"],
	)

	if not sdn_rows:
		return

	for sdn_row in sdn_rows:
		dn_name = sdn_row.delivery_note
		if not dn_name:
			continue

		dn_items = frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn_name},
			fields=["name", "item_code", "item_name", "qty", "stock_uom"],
		)

		# Only auto-populate when there is exactly one DN item — otherwise
		# we cannot know which DN item corresponds to this SDN row.
		if len(dn_items) != 1:
			continue

		item = dn_items[0]
		frappe.db.set_value(
			"Shipment Delivery Note",
			sdn_row.name,
			{
				"dn_detail": item.name,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"stock_uom": item.stock_uom,
			},
			update_modified=False,
		)
