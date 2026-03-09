// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shipment Quotation', {
	refresh(frm) {
		// Set Accept Quote to read-only if Shipment already has pickup scheduled
		frm
			.call({
				doc: frm.doc,
				method: 'check_if_shipment_pickup_scheduled',
			})
			.done(r => {
				if (r && r.pickup_scheduled) {
					frm.set_df_property('accept_quote', 'read_only', 1)
				}
			})
	},
})
