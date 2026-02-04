frappe.ui.form.on('Shipment Parcel Template', {
	refresh(frm) {
		if (frappe.user.has_role('System Manager')) {
			frm.add_custom_button('Sync from ShipStation', () => {
				frappe.call({
					method:
						'shipstation_integration.shipstation_integration.overrides.shipment_parcel_template.sync_shipstation_packages',
					callback() {
						frappe.msgprint('ShipStation packages synced')
					},
				})
			})
		}
	},
})
