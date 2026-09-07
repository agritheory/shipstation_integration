// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

const SHIPPING_ACCOUNTS_FIELD = 'shipping_accounts'

frappe.ui.form.on('Shipping Account', {
	shipping_accounts_add(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (row.carrier) {
			load_carrier_services_for_row(frm, cdt, cdn, row.carrier)
		}
	},

	form_render(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (row.carrier) {
			load_carrier_services_for_row(frm, cdt, cdn, row.carrier)
		}
	},

	carrier(frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		frappe.model.set_value(cdt, cdn, 'carrier_service', '')
		load_carrier_services_for_row(frm, cdt, cdn, row.carrier)
	},
})

frappe.ui.form.on('Customer', {
	refresh(frm) {
		refresh_shipping_account_service_options(frm)
	},
})

frappe.ui.form.on('Supplier', {
	refresh(frm) {
		refresh_shipping_account_service_options(frm)
	},
})

function refresh_shipping_account_service_options(frm) {
	const grid = frm.fields_dict[SHIPPING_ACCOUNTS_FIELD]?.grid
	if (!grid) return
	;(frm.doc[SHIPPING_ACCOUNTS_FIELD] || []).forEach(row => {
		if (row.carrier && grid.grid_rows_by_docname[row.name]) {
			load_carrier_services_for_row(frm, row.doctype, row.name, row.carrier)
		}
	})
}

function load_carrier_services_for_row(frm, cdt, cdn, carrier) {
	if (!carrier) {
		apply_carrier_service_options(frm, cdn, empty_carrier_service_options())
		return
	}

	frm.shipping_account_service_cache = frm.shipping_account_service_cache || {}
	if (frm.shipping_account_service_cache[carrier]) {
		apply_carrier_service_options(frm, cdn, frm.shipping_account_service_cache[carrier])
		return
	}

	frappe
		.xcall('shipstation_integration.api.carriers.get_services_for_supplier', {
			supplier_name: carrier,
		})
		.then(services => {
			const options = empty_carrier_service_options().concat(
				(services || []).map(s => ({
					value: s.name || s.service_code,
					description: s.service_code || '',
				}))
			)
			frm.shipping_account_service_cache[carrier] = options
			apply_carrier_service_options(frm, cdn, options)
		})
}

function empty_carrier_service_options() {
	return [{ value: '', description: '' }]
}

function apply_carrier_service_options(frm, cdn, options) {
	const grid = frm.fields_dict[SHIPPING_ACCOUNTS_FIELD]?.grid
	if (!grid) return

	const row = grid.grid_rows_by_docname[cdn]
	if (!row?.columns?.carrier_service) return

	row.columns.carrier_service.df.options = options
	const field = row.columns.carrier_service.field
	if (field?.set_data) {
		field.set_data(options)
	}
}
