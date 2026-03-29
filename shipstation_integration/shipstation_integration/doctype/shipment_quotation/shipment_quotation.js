// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shipment Quotation', {
	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			check_if_shipment_pickup_scheduled(frm)
		}
	},
})

async function check_if_shipment_pickup_scheduled(frm) {
	// Disable submit if the parent Shipment already has a pickup scheduled
	await frappe
		.xcall(
			'shipstation_integration.shipstation_integration.doctype.shipment_quotation.shipment_quotation.check_if_shipment_pickup_scheduled',
			{ doc: frm.doc }
		)
		.then(r => {
			if (r && r.pickup_scheduled) {
				frm.disable_save()
				frappe.show_alert({
					message: __('This shipment already has a pickup scheduled. The quotation cannot be changed.'),
					indicator: 'orange',
				})
			}
		})
}
