# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Drop Shipment Delivery Note item columns now owned by Inventory Tools."""

import frappe

SHIPMENT_DELIVERY_NOTE_FIELDS = (
	"dn_detail",
	"item_code",
	"item_name",
	"qty",
	"stock_uom",
)


def execute():
	for fieldname in SHIPMENT_DELIVERY_NOTE_FIELDS:
		custom_field_name = f"Shipment Delivery Note-{fieldname}"
		if frappe.db.get_value("Custom Field", custom_field_name, "module") != "Shipstation Integration":
			continue
		frappe.delete_doc_if_exists("Custom Field", custom_field_name)

	frappe.clear_cache(doctype="Shipment Delivery Note")
