frappe.ui.form.on('Shipment Parcel Template', {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.add_custom_button(__('Sync to ShipStation'), () => {
				frm.call({
					method:
						'shipstation_integration.shipstation_integration.overrides.shipment_parcel_template.sync_parcel_template',
					args: {
						template_name: frm.doc.name,
					},
					freeze: true,
					freeze_message: __('Syncing package with ShipStation…'),
					callback: function (r) {
						frm.reload_doc()
					},
				})
			})
		}
	},
})
