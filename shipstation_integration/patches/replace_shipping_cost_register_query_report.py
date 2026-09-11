# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe


def execute():
	if not frappe.db.exists("Report", "Shipping Cost Register"):
		return

	report = frappe.get_doc("Report", "Shipping Cost Register")
	if report.is_standard == "Yes":
		return

	frappe.delete_doc("Report", "Shipping Cost Register", force=1)
	frappe.reload_doc("shipstation_integration", "report", "shipping_cost_register", force=True)
