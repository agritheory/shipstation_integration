// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.pages['tracking-number-map'].on_page_load = function () {
	frappe.route_flags.replace_route = true
	frappe.set_route('tracking-number', 'view', 'map')
}
