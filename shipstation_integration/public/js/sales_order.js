// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Sales Order', {
	refresh: frm => {
		shipping.shipstation(frm)
	},
})
