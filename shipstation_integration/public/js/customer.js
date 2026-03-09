// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Customer', {
	setup(frm) {
		frm.set_query('carrier', 'shipping_accounts', function () {
			return {
				filters: {
					is_transporter: 1,
				},
			}
		})
	},
})
