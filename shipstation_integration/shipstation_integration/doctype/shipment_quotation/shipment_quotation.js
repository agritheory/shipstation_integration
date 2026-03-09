// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shipment Quotation', {
	refresh(frm) {
		check_if_shipment_pickup_scheduled(frm)
	},
})

async function check_if_shipment_pickup_scheduled(frm) {
	// Sets Accept Quote to read-only if Shipment already has pickup scheduled
	await frappe
		.xcall(
			'shipstation_integration.shipstation_integration.doctype.shipment_quotation.shipment_quotation.check_if_shipment_pickup_scheduled',
			{
				doc: frm.doc,
			}
		)
		.then(r => {
			if (r && r.pickup_scheduled) {
				frm.set_df_property('accept_quote', 'read_only', 1)
				frm.refresh_field('accept_quote')
			}
		})
}
