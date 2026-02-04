frappe.ui.form.on('Shipment', {
	add_template(frm) {
		if (!frm.doc.parcel_template) return

		frappe.model.with_doc('Shipment Parcel Template', frm.doc.parcel_template, () => {
			let tpl = frappe.model.get_doc('Shipment Parcel Template', frm.doc.parcel_template)

			let row = frappe.model.add_child(frm.doc, 'Shipment Parcel', 'shipment_parcel')

			row.length = tpl.length
			row.width = tpl.width
			row.height = tpl.height
			row.weight = tpl.weight
			row.carrier = tpl.carrier
			row.package_code = tpl.package_code

			frm.refresh_fields('shipment_parcel')
		})
	},
})
