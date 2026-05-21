// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Tracking Number', {
	refresh(frm) {
		frm.set_df_property('tracking_number_event', 'cannot_add_rows', true)
		frm.toggle_display('section_tracking_number_event', frm.doc.docstatus)
		frm.toggle_display('section_map', frm.doc.docstatus)
	},
})
