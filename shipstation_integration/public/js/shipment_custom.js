// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shipment', {
	onload: frm => {
		frm.set_df_property('pallets', 'hidden', 1) // Replaced with Package Type field
		if (frm.doc.freight_type === 'LTL') {
			get_accessorial_services(frm)
		}
	},

	refresh: frm => {
		if (frm.doc.freight_type === 'LTL') {
			get_accessorial_services(frm)
			get_ltl_package_type_options(frm)
		}
		set_query_for_shipment_dimension_uoms(frm)
		get_carrier_service_levels(frm)
		show_quote_and_spot_quote_fields(frm)
		add_schedule_pickup_button(frm)
	},

	setup: frm => {
		frm.set_query('preferred_carrier', function () {
			return {
				filters: {
					is_transporter: 1,
				},
			}
		})
		set_query_for_shipment_dimension_uoms(frm)
	},

	validate: frm => {
		frm.trigger('calculate_total_dimensions')
	},

	preferred_carrier: frm => {
		if (frm.doc.freight_type === 'LTL') {
			get_ltl_carrier_id(frm).then(() => get_ltl_package_type_options(frm))
			get_carrier_service_levels(frm)
			get_accessorial_services(frm)
			show_quote_and_spot_quote_fields(frm)
		}
	},

	freight_type: frm => {
		show_quote_and_spot_quote_fields(frm)
		add_schedule_pickup_button(frm)
	},

	quote_or_offer_id: frm => {
		show_quote_and_spot_quote_fields(frm)
		add_schedule_pickup_button(frm)
	},

	calculate_total_dimensions: frm => {
		// Calculates total length/width/height/count if not already set
		if (frm.doc.shipment_parcel.length > 0) {
			if (frm.doc.shipment_parcel.length == 1) {
				let row = frm.doc.shipment_parcel[0]
				frm.doc.total_length = frm.doc.total_length || row.length
				frm.doc.total_width = frm.doc.total_width || row.width
				frm.doc.total_height = frm.doc.total_height || row.height
				frm.doc.total_number_of_packages_or_handling_units =
					frm.doc.total_number_of_packages_or_handling_units || row.count
				frm.refresh_fields([
					'total_length',
					'total_width',
					'total_height',
					'total_number_of_packages_or_handling_units',
				])
			} else {
				let total_count = 0
				frm.doc.shipment_parcel.forEach(row => {
					total_count = total_count + row.count
				})
				// TODO: apply containerization to calculate dimensions if >1 HU/parcel?
				frm.doc.total_number_of_packages_or_handling_units =
					frm.doc.total_number_of_packages_or_handling_units || total_count
				frm.refresh_fields([
					'total_length',
					'total_width',
					'total_height',
					'total_number_of_packages_or_handling_units',
				])
			}
		}
	},
})

frappe.ui.form.on('Shipment Parcel', {
	shipment_parcel_add: (frm, cdt, cdn) => {
		if (frm.doc.freight_type === 'LTL') {
			get_ltl_package_type_options(frm)
		}
	},
	form_render: (frm, cdt, cdn) => {
		if (frm.doc.freight_type === 'LTL') {
			get_ltl_package_type_options(frm)
		}
	},
})

async function get_ltl_carrier_id(frm) {
	if (frm.doc.carrier_id) {
		return
	}
	await frappe
		.xcall('shipstation_integration.shipstation_integration.overrides.shipment.get_carrier_id_for_supplier', {
			supplier_name: frm.doc.preferred_carrier,
			settings_name: null,
		})
		.then(r => {
			if (r) {
				frappe.model.set_value(frm.doc.doctype, frm.doc.name, 'carrier_id', r)
			}
		})
}

async function get_ltl_package_type_options(frm) {
	if (!frm.doc.carrier_id) return
	await frappe
		.xcall('shipstation_integration.shipstation_integration.overrides.shipment.get_ltl_package_type_options', {
			carrier_id: frm.doc.carrier_id,
			settings_name: null,
		})
		.then(options => {
			// Populate the Shipment-level package_type_code dropdown with carrier-specific options
			const select_options = ['', ...options.map(o => o.value)].join('\n')
			frm.set_df_property('package_type_code', 'options', select_options)
			frm.refresh_field('package_type_code')
		})
}

async function set_query_for_shipment_dimension_uoms(frm) {
	await frappe
		.xcall('shipstation_integration.shipstation_integration.overrides.shipment.get_shipment_dimension_uoms', {
			settings_name: null,
		})
		.then(r => {
			if (r) {
				if (r.length_uom.length >= 1) {
					frm.set_query('length_uom', 'shipment_parcel', function () {
						return {
							filters: {
								name: ['in', r.length_uom],
							},
						}
					})
				}
				if (r.weight_uom.length >= 1) {
					frm.set_query('weight_uom', 'shipment_parcel', function () {
						return {
							filters: {
								name: ['in', r.weight_uom],
							},
						}
					})
				}
				if (r.density_uom.length >= 1) {
					frm.set_query('density_uom', 'shipment_parcel', function () {
						return {
							filters: {
								name: ['in', r.density_uom],
							},
						}
					})
				}
			}
		})
}

async function get_carrier_service_levels(frm) {
	await frappe
		.xcall('shipstation_integration.shipstation_integration.overrides.shipment.get_carrier_service_levels', {
			doc: frm.doc,
			settings_name: null,
		})
		.then(r => {
			if (r) {
				frm.set_df_property('carrier_service_level', 'options', r)
				frm.refresh_field('carrier_service_level')
			}
		})
}

async function get_accessorial_services(frm) {
	await frappe
		.xcall(
			'shipstation_integration.shipstation_integration.overrides.shipment.get_supported_accessorial_service_fields',
			{ doc: frm.doc, settings_name: null }
		)
		.then(r => {
			if (r) {
				r.supported.forEach(field => {
					frm.set_df_property(field, 'read_only', 0)
					frm.set_df_property(field, 'hidden', 0)
					frm.refresh_field(field)
				})
				r.unsupported.forEach(field => {
					frm.set_df_property(field, 'read_only', 1)
					frm.set_df_property(field, 'hidden', 1)
					frappe.model.set_value(frm.doc.doctype, frm.doc.name, field, 0)
					frm.refresh_field(field)
				})
			}
		})
}

async function show_quote_and_spot_quote_fields(frm) {
	await frappe
		.xcall('shipstation_integration.shipstation_integration.overrides.shipment.supports_quote_or_spot_quote', {
			doc: frm.doc,
			settings_name: null,
		})
		.then(r => {
			if (r) {
				if (!r.supports_spot_quote) {
					frappe.model.set_value(frm.doc.doctype, frm.doc.name, 'request_spot_quote', 0)
					frm.set_df_property('request_spot_quote', 'hidden', 1)
				} else {
					frm.set_df_property('request_spot_quote', 'hidden', 0)
				}
				frm.refresh_field('request_spot_quote')

				if (
					(r.supports_quote || r.supports_spot_quote) &&
					frm.doc.freight_type === 'LTL' &&
					!frm.doc.quote_or_offer_id
				) {
					frm.add_custom_button(__('Get LTL Quotes'), () => {
						frappe.call({
							method: 'shipstation_integration.shipstation_integration.overrides.shipment.get_ltl_quotes',
							args: {
								doc: frm.doc,
								settings_name: null,
							},
							freeze: true,
							callback: function (r) {
								if (r) {
									frappe.msgprint(__(r))
								}
							},
						})
					})
				} else {
					frm.remove_custom_button('Get LTL Quotes')
				}
			}
		})
}

function add_schedule_pickup_button(frm) {
	if (frm.doc.freight_type === 'LTL' && frm.doc.quote_or_offer_id && !(frm.doc.pickup_id || frm.doc.awb_number)) {
		frm.add_custom_button(__('Schedule LTL Pickup'), () => {
			frappe.call({
				method: 'shipstation_integration.shipstation_integration.overrides.shipment.schedule_ltl_pickup',
				args: {
					doc: frm.doc,
					settings_name: null,
				},
				freeze: true,
				callback: function (r) {
					if (r) {
						frappe.msgprint(__(r))
					}
				},
			})
		})
	} else {
		frm.remove_custom_button('Schedule LTL Pickup')
	}
}
