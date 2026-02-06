frappe.ui.form.on('Shipment Parcel Template', {
	refresh(frm) {
		set_labels(frm)
	},
})

function set_labels(frm) {
	const uom = frappe.boot.parcel_uom || {}

	const dimension_uom = uom.dimension_uom || 'Centimeter'
	const weight_uom = uom.weight_uom || 'Kilogram'

	frm.set_df_property('length_display', 'label', `Length (${dimension_uom})`)
	frm.set_df_property('width_display', 'label', `Width (${dimension_uom})`)
	frm.set_df_property('height_display', 'label', `Height (${dimension_uom})`)
	frm.set_df_property('weight_display', 'label', `Weight (${weight_uom})`)
}
