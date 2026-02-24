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
		frm.trigger('enable_legacy_api')

		if (frm.doc.enable_legacy_api && frm.doc.carrier_data) {
			const wrapper = $(frm.fields_dict.carriers_html.wrapper)
			wrapper.html(
				frappe.render_template('carriers', {
					carriers: frm.doc.__onload.carriers,
				})
			)
		}

		// Show API v2 carrier count and render carrier HTML if available
		if (frm.doc.enable_shipstation_api && frm.doc.shipstation_api_carrier_data) {
			try {
				const api_carriers = JSON.parse(frm.doc.shipstation_api_carrier_data)
				if (api_carriers.length > 0) {
					frm.dashboard.add_indicator(__('API v2 Carriers: {0}', [api_carriers.length]), 'blue')

					// Count total packages
					let total_packages = 0
					api_carriers.forEach(c => {
						total_packages += (c.packages || []).length
					})
					if (total_packages > 0) {
						frm.dashboard.add_indicator(__('Package Types: {0}', [total_packages]), 'green')
					}

					// Render API carriers HTML
					frm.trigger('render_api_carriers_html')
				}
			} catch (e) {
				// Ignore parse errors
			}
		}
	},

	render_api_carriers_html: frm => {
		if (!frm.doc.shipstation_api_carrier_data) return

		try {
			const carriers = JSON.parse(frm.doc.shipstation_api_carrier_data)
			const wrapper = $(frm.fields_dict.api_carriers_html.wrapper)

			let html = '<div class="api-carriers-container">'
			html += '<h5 class="text-muted">' + __('API v2 Carriers & Package Types') + '</h5>'

			carriers.forEach(carrier => {
				html += `<div class="carrier-card" style="border: 1px solid var(--border-color); border-radius: 8px; padding: 12px; margin-bottom: 12px;">`
				html += `<div style="display: flex; justify-content: space-between; align-items: center;">`
				html += `<strong>${carrier.name || carrier.carrier_code}</strong>`
				html += `<span class="text-muted">${carrier.carrier_id}</span>`
				html += `</div>`

				// Services
				if (carrier.services && carrier.services.length > 0) {
					html += `<div style="margin-top: 8px;">`
					html += `<small class="text-muted">${__('Services')}: ${carrier.services.length}</small>`
					html += `</div>`
				}

				// Packages
				if (carrier.packages && carrier.packages.length > 0) {
					html += `<div style="margin-top: 8px;">`
					html += `<small class="text-muted">${__('Package Types')}:</small>`
					html += `<ul style="margin: 4px 0 0 16px; padding: 0;">`
					carrier.packages.forEach(pkg => {
						let dims = ''
						if (pkg.dimensions && pkg.dimensions.length) {
							dims = ` (${pkg.dimensions.length}x${pkg.dimensions.width}x${pkg.dimensions.height} ${
								pkg.dimensions.unit || 'in'
							})`
						}
						html += `<li style="font-size: 12px;"><code>${pkg.package_code}</code> - ${pkg.name || ''}${dims}</li>`
					})
					html += `</ul>`
					html += `</div>`
				}

				html += `</div>`
			})

			html += '</div>'
			wrapper.html(html)
		} catch (e) {
			console.error('Error rendering API carriers:', e)
		}
	},

	update_carriers_and_stores: frm => {
		if (!frm.doc.enable_legacy_api) {
			frappe.msgprint(__('Please enable Legacy API (v1) first.'))
			return
		}
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
		if (!frm.doc.enable_legacy_api) {
			frappe.msgprint(__('Please enable Legacy API (v1) first.'))
			return
		}
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
		if (!frm.doc.enable_legacy_api) {
			frappe.msgprint(__('Please enable Legacy API (v1) first.'))
			return
		}
		frappe.show_alert(__('Getting Orders'))
		frm.call({
			doc: frm.doc,
			method: 'get_orders',
			freeze: true,
		})
	},

	get_shipments: frm => {
		if (!frm.doc.enable_legacy_api) {
			frappe.msgprint(__('Please enable Legacy API (v1) first.'))
			return
		}
		frappe.show_alert(__('Getting Shipments'))
		frm.call({
			doc: frm.doc,
			method: 'get_shipments',
			freeze: true,
		})
	},

	get_tags: frm => {
		if (!frm.doc.enable_legacy_api) {
			frappe.msgprint(__('Please enable Legacy API (v1) first.'))
			return
		}
		frappe.show_alert(__('Getting Tags'))
		frm.call({
			doc: frm.doc,
			method: 'get_tags',
			freeze: true,
		})
	},

	fetch_warehouses: frm => {
		if (!frm.doc.enable_legacy_api) {
			frappe.msgprint(__('Please enable Legacy API (v1) first.'))
			return
		}
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

	sync_carrier_packages: frm => {
		if (!frm.doc.enable_shipstation_api) {
			frappe.msgprint(__('Please enable ShipStation API v2 first.'))
			return
		}
		frappe.show_alert(__('Syncing carrier package types with dimensions...'))
		frm
			.call({
				doc: frm.doc,
				method: 'sync_carrier_packages',
				freeze: true,
				freeze_message: __('Fetching detailed package types from all carriers...'),
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
		frm.toggle_display('sync_carrier_packages', frm.doc.enable_shipstation_api && !frm.is_new())
	},

	enable_legacy_api: frm => {
		// Show/hide legacy API fields based on checkbox
		frm.toggle_display('api_key', frm.doc.enable_legacy_api)
		frm.toggle_display('api_secret', frm.doc.enable_legacy_api)
		frm.toggle_display('get_items', frm.doc.enable_legacy_api && !frm.is_new())
		frm.toggle_display('get_orders', frm.doc.enable_legacy_api && !frm.is_new())
		frm.toggle_display('get_shipments', frm.doc.enable_legacy_api && !frm.is_new())
		frm.toggle_display('get_tags', frm.doc.enable_legacy_api && !frm.is_new())
		frm.toggle_display('update_carriers_and_stores', frm.doc.enable_legacy_api && !frm.is_new())
		frm.toggle_display('fetch_warehouses', frm.doc.enable_legacy_api && !frm.is_new())
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
