// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shipment Parcel Template', {
	refresh(frm) {
		set_labels(frm)

		if (!frm.is_new() && !frm.doc.skip_shipstation_sync) {
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

function set_labels(frm) {
	const uom = frappe.boot.parcel_uom || {}

	const dimension_uom = uom.dimension_uom || 'Centimeter'
	const weight_uom = uom.weight_uom || 'Kg'

	frm.set_df_property('length_display', 'label', `Length (${dimension_uom})`)
	frm.set_df_property('width_display', 'label', `Width (${dimension_uom})`)
	frm.set_df_property('height_display', 'label', `Height (${dimension_uom})`)
	frm.set_df_property('weight_display', 'label', `Weight (${weight_uom})`)
}
