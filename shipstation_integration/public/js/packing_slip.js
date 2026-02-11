// Copyright (c) 2024, AgriTheory and contributors
// For license information, please see license.txt

/**
 * Check if a value is empty or MySQL NULL placeholder
 * MySQL exports NULL as \N which may have been imported as literal text
 */
function is_empty_or_null(value) {
	return !value || value === '\\N' || value === 'NULL' || value === 'None'
}

/**
 * Clean a value, converting MySQL NULL placeholders to empty string
 */
function clean_value(value) {
	if (is_empty_or_null(value)) {
		return ''
	}
	return value
}

frappe.ui.form.on('Packing Slip', {
	setup: function (frm) {
		// Set query for shipping address - filter by customer linked via Delivery Note
		frm.set_query('shipping_address_name', function () {
			if (frm.doc.delivery_note) {
				return {
					query: 'frappe.contacts.doctype.address.address.address_query',
					filters: {
						link_doctype: 'Customer',
						link_name: frm.doc.__onload?.customer,
					},
				}
			}
			return {}
		})

		// Set query for dispatch address - filter by company
		frm.set_query('dispatch_address_name', function () {
			return {
				filters: {
					is_your_company_address: 1,
				},
			}
		})

		// Set query for carrier in Parcel Dimensions - only transporters
		frm.set_query('carrier', function () {
			return {
				filters: {
					is_transporter: 1,
				},
			}
		})

		// Set query for parcel_template in Parcel Dimensions - respect carrier if selected
		frm.set_query('parcel_template', 'parcel_dimensions', function (doc, cdt, cdn) {
			const row = locals[cdt][cdn]
			if (frm.doc.carrier) {
				return {
					filters: {
						carrier: frm.doc.carrier,
					},
				}
			}
			return {}
		})
	},

	refresh: function (frm) {
		// Load carrier services if carrier is already set (ensures service_code_map is populated)
		if (frm.doc.carrier && !frm.service_code_map) {
			load_carrier_services(frm, frm.doc.carrier)
		}

		setup_shipping_actions(frm)
		set_uom_labels(frm)
	},

	delivery_note: function (frm) {
		if (!frm.doc.delivery_note) return

		if (frm.__delivery_note_loaded) return
		frm.__delivery_note_loaded = true

		// Fetch customer, addresses, and weight info from Delivery Note
		frappe.call({
			method: 'frappe.client.get',
			args: {
				doctype: 'Delivery Note',
				name: frm.doc.delivery_note,
			},
			callback: function (r) {
				if (r.message) {
					const dn = r.message

					// Store customer for address query
					frm.doc.__onload = frm.doc.__onload || {}
					frm.doc.__onload.customer = dn.customer

					// Set shipping address (destination) from DN if not already set
					const dn_shipping_addr = clean_value(dn.shipping_address_name)
					if (is_empty_or_null(frm.doc.shipping_address_name) && dn_shipping_addr) {
						frm.set_value('shipping_address_name', dn_shipping_addr)
					}

					// Set dispatch address (source/company) from DN if not already set
					const dn_company_addr = clean_value(dn.company_address)
					if (is_empty_or_null(frm.doc.dispatch_address_name) && dn_company_addr) {
						frm.set_value('dispatch_address_name', dn_company_addr)
					}

					// Calculate and set weight from DN items
					fetch_weight_from_delivery_note(frm, dn)
				}
			},
		})
	},

	shipping_address_name: function (frm) {
		const addr = frm.doc.shipping_address_name
		if (addr) {
			frappe.call({
				method: 'frappe.contacts.doctype.address.address.get_address_display',
				args: {
					address_dict: addr,
				},
				callback: function (r) {
					if (r.message) {
						frm.set_value('shipping_address', r.message)
					}
				},
			})
		} else {
			frm.set_value('shipping_address', '')
		}
	},

	dispatch_address_name: function (frm) {
		const addr = frm.doc.dispatch_address_name
		if (addr) {
			frappe.call({
				method: 'frappe.contacts.doctype.address.address.get_address_display',
				args: {
					address_dict: addr,
				},
				callback: function (r) {
					if (r.message) {
						frm.set_value('dispatch_address', r.message)
					}
				},
			})
		} else {
			frm.set_value('dispatch_address', '')
		}
	},

	carrier: function (frm) {
		// When carrier changes, load available services with friendly names
		if (frm.doc.carrier) {
			load_carrier_services(frm, frm.doc.carrier)
		} else {
			// Reset to Data field if no carrier
			frm.set_df_property('carrier_service', 'fieldtype', 'Data')
			frm.set_df_property('carrier_service', 'options', null)
			frm.service_code_map = null
			frm.refresh_field('carrier_service')
		}
	},
})

/**
 * Load carrier services and populate service_code_map
 * This is called both when carrier changes and on refresh if carrier is already set
 */
function load_carrier_services(frm, supplier_name) {
	frappe.call({
		method: 'shipstation_integration.carriers.get_services_for_supplier',
		args: {
			supplier_name: supplier_name,
		},
		callback: function (r) {
			if (r.message && r.message.length > 0) {
				// Build options with friendly names
				const options = [''].concat(r.message.map(s => s.name || s.service_code))

				// Store mapping for lookup when creating label
				// Maps friendly name -> service_code
				frm.service_code_map = {}
				r.message.forEach(s => {
					const label = s.name || s.service_code
					frm.service_code_map[label] = s.service_code
				})

				frm.set_df_property('carrier_service', 'options', options.join('\n'))
				frm.set_df_property('carrier_service', 'fieldtype', 'Select')
				frm.refresh_field('carrier_service')
			}
		},
	})
}

function fetch_weight_from_delivery_note(frm, dn) {
	let net_weight = 0

	// Calculate total weight from DN items
	;(dn.items || []).forEach(item => {
		net_weight += flt(item.total_weight) || flt(item.net_weight) * flt(item.qty) || 0
	})

	// Set weight UOM from first item if available
	const weight_uom = dn.items?.[0]?.weight_uom || 'Pound'

	frm.set_value('net_weight_pkg', flt(net_weight, 2))
	frm.set_value('net_weight_uom', weight_uom)

	if (!frm.doc.gross_weight_pkg) {
		frm.set_value('gross_weight_pkg', flt(net_weight, 2))
		frm.set_value('gross_weight_uom', weight_uom)
	}
}

function setup_shipping_actions(frm) {
	// Only add shipping buttons if ShipStation integration is enabled
	frappe.call({
		method: 'frappe.client.get_value',
		args: {
			doctype: 'Shipstation Settings',
			filters: { enabled: 1 },
			fieldname: ['name', 'enable_shipstation_api'],
		},
		callback: function (r) {
			if (!r.message || !r.message.enable_shipstation_api) return

			// Add label generation button if tracking not already present
			if (!has_tracking_number(frm)) {
				// Check if we have carrier info to create label directly
				const has_carrier = frm.doc.carrier || get_carrier_from_parcel_dimensions(frm)
				const has_service = frm.doc.carrier_service

				if (has_carrier && has_service) {
					// Can create label directly
					frm.add_custom_button(__('Create Label'), () => create_label_direct(frm), __('Shipping'))
				} else {
					// Need to select carrier/service first
					frm.add_custom_button(__('Create Label'), () => create_shipping_label(frm), __('Shipping'))
				}

				// Rate shopping as secondary option
				frm.add_custom_button(__('Compare Rates'), () => get_shipping_rates(frm), __('Shipping'))
			}
		},
	})
}

function get_carrier_from_parcel_dimensions(frm) {
	if (frm.doc.parcel_dimensions && frm.doc.parcel_dimensions.length > 0) {
		return frm.doc.parcel_dimensions[0].carrier
	}
	return null
}

function has_tracking_number(frm) {
	if (!frm.doc.parcel_dimensions || frm.doc.parcel_dimensions.length === 0) {
		return false
	}
	return frm.doc.parcel_dimensions.some(row => row.tracking_number)
}

function get_shipping_rates(frm) {
	if (is_empty_or_null(frm.doc.shipping_address_name)) {
		frappe.msgprint(__('Please select a shipping address first.'))
		return
	}

	if (is_empty_or_null(frm.doc.dispatch_address_name)) {
		frappe.msgprint(__('Please select a dispatch (ship from) address first.'))
		return
	}

	if (!frm.doc.parcel_dimensions || frm.doc.parcel_dimensions.length === 0) {
		frappe.msgprint(__('Please add parcel dimensions first.'))
		return
	}

	// Call the rate shopping API for this Packing Slip
	frappe.call({
		method: 'shipstation_integration.rates.get_rates_for_packing_slip',
		args: {
			packing_slip: frm.doc.name,
		},
		freeze: true,
		freeze_message: __('Fetching shipping rates...'),
		callback: function (r) {
			if (r.message && r.message.rates) {
				show_rates_dialog(frm, r.message.rates)
			} else {
				frappe.msgprint(__('No rates returned. Please check carrier configuration.'))
			}
		},
		error: function (err) {
			frappe.msgprint(__('Error fetching rates: {0}', [err.message || 'Unknown error']))
		},
	})
}

/**
 * Show dialog with available shipping rates
 */
function show_rates_dialog(frm, rates) {
	const rate_options = rates.map(r => ({
		value: JSON.stringify({ carrier_id: r.carrier_id, service_code: r.service_code }),
		label: `${r.carrier_name} - ${r.service_type}: $${r.shipping_amount?.amount || r.total_amount}`,
	}))

	const dialog = new frappe.ui.Dialog({
		title: __('Available Shipping Rates'),
		fields: [
			{
				fieldtype: 'HTML',
				fieldname: 'rates_html',
				options: build_rates_html(rates),
			},
			{
				fieldtype: 'Select',
				fieldname: 'selected_rate',
				label: __('Select Rate'),
				options: rate_options,
				reqd: 1,
			},
		],
		primary_action_label: __('Create Label'),
		primary_action: function () {
			const selected = JSON.parse(dialog.get_value('selected_rate'))
			dialog.hide()
			create_label_with_rate(frm, selected.carrier_id, selected.service_code)
		},
	})

	dialog.show()
}

/**
 * Build HTML table for rates display
 */
function build_rates_html(rates) {
	let html = '<table class="table table-bordered table-sm">'
	html += '<thead><tr><th>Carrier</th><th>Service</th><th>Est. Days</th><th>Cost</th></tr></thead>'
	html += '<tbody>'

	rates.forEach(rate => {
		html += `<tr>
			<td>${rate.carrier_name || rate.carrier_id}</td>
			<td>${rate.service_type || rate.service_code}</td>
			<td>${rate.delivery_days || '-'}</td>
			<td>$${rate.shipping_amount?.amount || rate.total_amount || '-'}</td>
		</tr>`
	})

	html += '</tbody></table>'
	return html
}

/**
 * Create shipping label with selected rate (carrier_id + service_code already known)
 */
function create_label_with_rate(frm, carrier_id, service_code) {
	frappe.call({
		method: 'shipstation_integration.labels.create_label_for_packing_slip',
		args: {
			packing_slip: frm.doc.name,
			carrier_id: carrier_id,
			service_code: service_code,
		},
		freeze: true,
		freeze_message: __('Creating shipping label...'),
		callback: function (r) {
			if (r.message) {
				show_label_success(frm, r.message)
			}
		},
		error: function (err) {
			frappe.msgprint(__('Error creating label: {0}', [err.message || 'Unknown error']))
		},
	})
}

/**
 * Create label directly using carrier/service from the Packing Slip form
 */
function create_label_direct(frm) {
	const carrier_supplier = frm.doc.carrier || get_carrier_from_parcel_dimensions(frm)
	const service_display = frm.doc.carrier_service

	if (!carrier_supplier) {
		frappe.msgprint(__('Please select a carrier first.'))
		return
	}

	if (!service_display) {
		frappe.msgprint(__('Please select a carrier service first.'))
		return
	}

	// Convert service display name to service_code if we have a mapping
	let service_code = service_display

	// Try to look up the actual service code from the friendly name
	if (frm.service_code_map && frm.service_code_map[service_display]) {
		service_code = frm.service_code_map[service_display]
	} else {
		// If no map, the value might already be a service_code (e.g., "ups_ground")
		// or we need to load services first - warn user if it looks like a friendly name
		if (service_display.includes(' ') || /[A-Z]/.test(service_display.charAt(0))) {
			// Looks like a friendly name, not a service code - reload services
			console.warn('Service code map not found, service_display may be friendly name:', service_display)
		}
	}

	// Look up ShipEngine carrier_id from Supplier name
	frappe.call({
		method: 'shipstation_integration.carriers.get_carrier_id_for_supplier',
		args: {
			supplier_name: carrier_supplier,
		},
		callback: function (r) {
			if (r.message) {
				create_label_with_rate(frm, r.message, service_code)
			} else {
				frappe.msgprint(
					__('Could not find ShipEngine carrier for {0}. Please sync carriers first.', [carrier_supplier])
				)
			}
		},
		error: function (err) {
			frappe.msgprint(__('Error looking up carrier: {0}', [err.message || 'Unknown error']))
		},
	})
}

/**
 * Show success message after label creation
 */
function show_label_success(frm, result) {
	let msg = __('Label created successfully!')
	if (result.tracking_number) {
		msg += '<br><br>' + __('Tracking Number: {0}', [result.tracking_number])
	}
	if (result.label_download) {
		msg += '<br><a href="' + result.label_download + '" target="_blank">' + __('Download Label') + '</a>'
	}
	frappe.msgprint({
		title: __('Shipping Label Created'),
		indicator: 'green',
		message: msg,
	})
	frm.reload_doc()
}

/**
 * Create shipping label (without rate selection - shows carrier dialog)
 */
function create_shipping_label(frm) {
	frappe.call({
		method: 'shipstation_integration.carriers.list_carriers',
		callback: function (r) {
			if (!r.message || r.message.length === 0) {
				frappe.msgprint(__('No carriers configured. Please set up carriers in Shipstation Settings.'))
				return
			}

			show_carrier_selection_dialog(frm, r.message)
		},
	})
}

/**
 * Show dialog for carrier/service selection with user-friendly names
 */
function show_carrier_selection_dialog(frm, carriers) {
	// Build carrier options with friendly names
	// Format: "carrier_id\nCarrier Name" for select display
	const carrier_options = carriers.map(c => ({
		value: c.carrier_id,
		label: c.name || c.friendly_name || c.carrier_code || c.carrier_id,
	}))

	// Store carriers for service lookup
	const carriers_map = {}
	carriers.forEach(c => {
		carriers_map[c.carrier_id] = c
	})

	const dialog = new frappe.ui.Dialog({
		title: __('Select Carrier and Service'),
		fields: [
			{
				fieldtype: 'Select',
				fieldname: 'shipping_account',
				label: __('Shipping Account Number'),
				options: [],
				onchange: () => {
					const acc = dialog.shipping_account_map?.[dialog.get_value('shipping_account')]
					if (acc) {
						dialog.set_value('carrier_id', acc.carrier)
						load_services_for_dialog(dialog, acc.carrier)
					}
				},
			},
			{
				fieldtype: 'Autocomplete',
				fieldname: 'carrier_id',
				label: __('Carrier'),
				options: carrier_options.map(c => c.label),
				reqd: 1,
				onchange: function () {
					const carrier_label = dialog.get_value('carrier_id')
					const carrier = carrier_options.find(c => c.label === carrier_label)
					if (carrier) {
						load_services_for_dialog(dialog, carrier.value)
					}
				},
			},
			{
				fieldtype: 'Autocomplete',
				fieldname: 'service_code',
				label: __('Service'),
				options: [],
				reqd: 1,
			},
		],
		primary_action_label: __('Create Label'),
		primary_action: function () {
			const carrier_label = dialog.get_value('carrier_id')
			const service_label = dialog.get_value('service_code')

			// Find carrier_id from label
			const carrier = carrier_options.find(c => c.label === carrier_label)
			if (!carrier) {
				frappe.msgprint(__('Please select a valid carrier.'))
				return
			}

			// Find service_code from label (stored in dialog data)
			const service_code = dialog.service_code_map?.[service_label] || service_label

			dialog.hide()
			create_label_with_rate(frm, carrier.value, service_code)
		},
	})

	dialog.show()
	load_shipping_accounts(frm, dialog)
}

function load_shipping_accounts(frm, dialog) {
	if (!frm.doc.delivery_note) return

	frappe.call({
		method: 'shipstation_integration.carriers.get_shipping_accounts',
		args: {
			delivery_note: frm.doc.delivery_note,
		},
		callback(r) {
			const accounts = r.message || []

			if (!accounts.length) {
				// hide field
				dialog.set_df_property('shipping_account', 'hidden', 1)
				return
			}

			// show field
			dialog.set_df_property('shipping_account', 'hidden', 0)

			const options = accounts.map(a => a.shipping_account_number)
			dialog.set_df_property('shipping_account', 'options', options)

			// map for carrier auto fill
			dialog.shipping_account_map = {}
			accounts.forEach(a => {
				dialog.shipping_account_map[a.shipping_account_number] = a
			})
		},
	})
}

/**
 * Load services for the carrier selection dialog
 */
function load_services_for_dialog(dialog, carrier_id) {
	frappe.call({
		method: 'shipstation_integration.carriers.list_carrier_services',
		args: { carrier_id: carrier_id },
		callback: function (r) {
			if (r.message && r.message.length > 0) {
				// Build service options with friendly names
				const service_options = r.message.map(s => s.name || s.service_code)

				// Store mapping of label -> service_code for lookup
				dialog.service_code_map = {}
				r.message.forEach(s => {
					const label = s.name || s.service_code
					dialog.service_code_map[label] = s.service_code
				})

				// For Autocomplete fields, use set_data() to update options
				const service_field = dialog.fields_dict.service_code
				if (service_field && service_field.set_data) {
					service_field.set_data(service_options)
				} else if (service_field && service_field.awesomplete) {
					service_field.awesomplete.list = service_options
				}
			} else {
				const service_field = dialog.fields_dict.service_code
				if (service_field && service_field.set_data) {
					service_field.set_data([])
				}
			}
		},
		error: function (err) {
			console.error('Error loading carrier services:', err)
		},
	})
}

function set_uom_labels(frm) {
	const uom = frappe.boot.parcel_uom || {}

	const length = uom.dimension_uom || 'Centimeter'
	const weight = uom.weight_uom || 'Kilogram'

	frm.fields_dict.parcel_dimensions.grid.update_docfield_property('length_display', 'label', `Length (${length})`)

	frm.fields_dict.parcel_dimensions.grid.update_docfield_property('width_display', 'label', `Width (${length})`)
	frm.fields_dict.parcel_dimensions.grid.update_docfield_property('height_display', 'label', `Height (${length})`)

	frm.fields_dict.parcel_dimensions.grid.update_docfield_property('weight_display', 'label', `Weight (${weight})`)
}

frappe.ui.form.on('Parcel Dimensions', {
	parcel_template: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (row.parcel_template) {
			frappe.db.get_doc('Shipment Parcel Template', row.parcel_template).then(template => {
				if (template) {
					// Set dimensions on the child row
					frappe.model.set_value(cdt, cdn, {
						carrier: template.carrier || '',
						length: template.length,
						width: template.width,
						height: template.height,
						weight: frm.doc.gross_weight_pkg || template.weight || 0,
						weight_uom: frm.doc.gross_weight_uom || get_weight_uom_from_template(),
						dimension_uom: get_dimension_uom_from_template(),
					})
					frm.refresh_field('parcel_dimensions')

					// Also set carrier on parent Packing Slip if not already set
					if (template.carrier && !frm.doc.carrier) {
						frm.set_value('carrier', template.carrier)
					}
				}
			})
		}
	},

	// When carrier changes on child row, sync to parent
	carrier: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (row.carrier && !frm.doc.carrier) {
			frm.set_value('carrier', row.carrier)
		}
	},

	length: function (frm, cdt, cdn) {
		check_template_match(frm, cdt, cdn)
	},

	width: function (frm, cdt, cdn) {
		check_template_match(frm, cdt, cdn)
	},

	height: function (frm, cdt, cdn) {
		check_template_match(frm, cdt, cdn)
	},
})

function get_dimension_uom_from_template() {
	// Shipment Parcel Template in ERPNext stores dimensions in cm
	return 'Centimeter'
}

function get_weight_uom_from_template() {
	// Default to Kilogram
	return 'Kilogram'
}

function get_default_dimension_uom() {
	// Could be made configurable per user/company in the future
	return 'Inch'
}

function get_default_weight_uom() {
	// Could be made configurable per user/company in the future
	return 'Pound'
}

function check_template_match(frm, cdt, cdn) {
	const row = locals[cdt][cdn]

	// Skip if no dimensions entered yet
	if (!row.length || !row.width || !row.height) {
		return
	}

	// Skip if template already selected
	if (row.parcel_template) {
		return
	}

	// Try to find a matching template
	frappe.call({
		method: 'shipstation_integration.utils.find_matching_parcel_template',
		args: {
			length: row.length,
			width: row.width,
			height: row.height,
			dimension_uom: row.dimension_uom,
		},
		callback: function (r) {
			if (r.message) {
				frappe.model.set_value(cdt, cdn, 'parcel_template', r.message)
				frm.refresh_field('parcel_dimensions')
			}
		},
	})
}
