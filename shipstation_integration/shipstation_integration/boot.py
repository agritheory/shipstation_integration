# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt
import frappe

from shipstation_integration.parcel_uom_conversion import prefetch_parcel_factors_for_boot


def boot_session(bootinfo):
	bootinfo.tags = frappe.get_all("Tag", ["name", "color"])

	user = frappe.get_cached_doc("User", frappe.session.user)

	dimension_pref = user.get("dimension_uom") or "Centimeter"
	weight_pref = user.get("weight_uom") or "Kg"

	factors = prefetch_parcel_factors_for_boot(dimension_pref, weight_pref)
	bootinfo.parcel_uom = {
		"dimension_uom": dimension_pref,
		"weight_uom": weight_pref,
		**factors,
	}

	bootinfo.inventory_tools_installed = "inventory_tools" in frappe.get_installed_apps()
