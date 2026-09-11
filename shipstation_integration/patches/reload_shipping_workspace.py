# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe


def execute():
	frappe.reload_doc("shipstation_integration", "workspace", "shipping", force=True)
