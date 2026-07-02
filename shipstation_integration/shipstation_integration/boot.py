# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt
import frappe


def boot_session(bootinfo):
	bootinfo.tags = frappe.get_all("Tag", ["name", "color"])

	user = frappe.get_cached_doc("User", frappe.session.user)

	bootinfo.parcel_uom = {
		"dimension_uom": user.get("dimension_uom") or "Centimeter",
		"weight_uom": user.get("weight_uom") or "Kilogram",
	}

	bootinfo.inventory_tools_installed = "inventory_tools" in frappe.get_installed_apps()
