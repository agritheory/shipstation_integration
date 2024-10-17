# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt
import frappe


def boot_session(bootinfo):
	bootinfo.tags = frappe.get_list("Tag", ["name", "color"])
