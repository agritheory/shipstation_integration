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
