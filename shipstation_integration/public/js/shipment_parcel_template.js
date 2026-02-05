frappe.ui.form.on('Shipment Parcel Template', {
	refresh(frm) {
		set_labels(frm)
	},
})

function set_labels(frm) {
	const uom = frappe.boot.parcel_uom || {}

	const length_uom = uom.length_uom || 'cm'
	const weight_uom = uom.weight_uom || 'kg'

	frm.set_df_property('length_display', 'label', `Length (${length_uom})`)
	frm.set_df_property('width_display', 'label', `Width (${length_uom})`)
	frm.set_df_property('height_display', 'label', `Height (${length_uom})`)
	frm.set_df_property('weight_display', 'label', `Weight (${weight_uom})`)
}
