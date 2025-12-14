// Copyright (c) 2024 AgriTheory and contributors
// For license information, please see license.txt

function company_query(frm, cdt, cdn) {
	const row = frm.selected_doc || locals[cdt][cdn]
	return {
		filters: {
			company: row.company,
			is_group: 0,
		},
	}
}

frappe.ui.form.on('Shipstation Settings', {
	setup: frm => {
		frm.set_query('shipstation_warehouses', {
			shipstation_warehouse_id: ['!=', ''],
		})
		frm.set_query('cost_center', 'shipstation_stores', company_query)
		frm.set_query('warehouse', 'shipstation_stores', company_query)
		frm.set_query('tax_account', 'shipstation_stores', company_query)
		frm.set_query('sales_account', 'shipstation_stores', company_query)
		frm.set_query('expense_account', 'shipstation_stores', company_query)
		frm.set_query('shipping_income_account', 'shipstation_stores', company_query)
		frm.set_query('shipping_expense_account', 'shipstation_stores', company_query)
	},

	after_save: frm => {
		frm.trigger('toggle_mandatory_table_fields')
	},

	refresh: frm => {
		frm.trigger('toggle_mandatory_table_fields')
		frm.trigger('enable_shipstation_api')

		if (frm.doc.carrier_data) {
			const wrapper = $(frm.fields_dict.carriers_html.wrapper)
			wrapper.html(
				frappe.render_template('carriers', {
					carriers: frm.doc.__onload.carriers,
				})
			)
		}

		// Show API v2 carrier count if available
		if (frm.doc.enable_shipstation_api && frm.doc.shipstation_api_carrier_data) {
			try {
				const api_carriers = JSON.parse(frm.doc.shipstation_api_carrier_data)
				if (api_carriers.length > 0) {
					frm.dashboard.add_indicator(
						__('API v2 Carriers: {0}', [api_carriers.length]),
						'blue'
					)
				}
			} catch (e) {
				// Ignore parse errors
			}
		}
	},

	update_carriers_and_stores: frm => {
		frappe.show_alert(__('Updating Carriers and Stores'))
		frm
			.call({
				doc: frm.doc,
				method: 'update_carriers_and_stores',
				freeze: true,
			})
			.done(() => {
				frm.reload_doc()
			})
	},

	get_items: frm => {
		frappe.show_alert(__('Getting Items'))
		frm
			.call({
				doc: frm.doc,
				method: 'get_items',
				freeze: true,
			})
			.done(r => {
				frappe.show_alert(r.message)
			})
	},

	get_orders: frm => {
		frappe.show_alert(__('Getting Orders'))
		frm.call({
			doc: frm.doc,
			method: 'get_orders',
			freeze: true,
		})
	},

	get_shipments: frm => {
		frappe.show_alert(__('Getting Shipments'))
		frm.call({
			doc: frm.doc,
			method: 'get_shipments',
			freeze: true,
		})
	},

	get_tags: frm => {
		frappe.show_alert(__('Getting Tags'))
		frm.call({
			doc: frm.doc,
			method: 'get_tags',
			freeze: true,
		})
	},

	fetch_warehouses: frm => {
		frm.call({
			doc: frm.doc,
			method: 'update_warehouses',
			freeze: true,
		})
	},

	reset_warehouses: frm => {
		frm.set_value('shipstation_warehouses', [])
		frm.save()
	},

	// ShipStation API v2 handlers
	test_api_connection: frm => {
		if (!frm.doc.enable_shipstation_api) {
			frappe.msgprint(__('Please enable ShipStation API v2 first.'))
			return
		}
		frappe.show_alert(__('Testing API connection...'))
		frm.call({
			doc: frm.doc,
			method: 'test_shipstation_api_connection',
			freeze: true,
		})
	},

	fetch_api_carriers: frm => {
		if (!frm.doc.enable_shipstation_api) {
			frappe.msgprint(__('Please enable ShipStation API v2 first.'))
			return
		}
		frappe.show_alert(__('Fetching API carriers...'))
		frm
			.call({
				doc: frm.doc,
				method: 'fetch_api_carriers',
				freeze: true,
			})
			.done(() => {
				frm.reload_doc()
			})
	},

	enable_shipstation_api: frm => {
		// Show/hide API key field based on checkbox
		frm.toggle_display('shipstation_api_key', frm.doc.enable_shipstation_api)
		frm.toggle_display('test_api_connection', frm.doc.enable_shipstation_api && !frm.is_new())
		frm.toggle_display('fetch_api_carriers', frm.doc.enable_shipstation_api && !frm.is_new())
	},

	toggle_mandatory_table_fields: frm => {
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('company', !frm.is_new())
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('warehouse', !frm.is_new())
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('cost_center', !frm.is_new())
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('shipping_income_account', !frm.is_new())
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('shipping_expense_account', !frm.is_new())
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('tax_account', !frm.is_new())
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('sales_account', !frm.is_new())
		frm.fields_dict.shipstation_stores.grid.toggle_reqd('expense_account', !frm.is_new())
	},
})
