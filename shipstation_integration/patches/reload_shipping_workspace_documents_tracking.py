# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe


def execute():
	frappe.reload_doc("shipstation_integration", "page", "tracking_number_map", force=True)
	frappe.reload_doc("shipstation_integration", "workspace", "shipping", force=True)
