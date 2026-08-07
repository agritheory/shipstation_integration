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
			company:
				frm.doc.pickup_from_type === 'Company'
					? frm.doc.pickup_company
					: frm.doc.delivery_to_type === 'Company'
						? frm.doc.delivery_company
						: null,
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
			shipment: frm.doc,
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
						const progress = start_ltl_quote_progress()
						frappe.call({
							method: 'shipstation_integration.shipstation_integration.overrides.shipment.fetch_ltl_quotes',
							args: { doc: frm.doc, settings_name: null },
							callback: function (r) {
								progress.finish()
								const quotes = r && r.message
								if (!quotes || !quotes.length) {
									frappe.msgprint(__('No LTL quotes returned for this shipment.'))
									return
								}
								show_ltl_quote_selection_dialog(frm, quotes)
							},
							error: () => progress.abandon(),
						})
					})
				} else {
					frm.remove_custom_button('Get LTL Quotes')
				}
			}
		})
}

// Carriers take the better part of a minute to return LTL rates, and the load balancer in front
// of the site closes the connection at 60 seconds. A plain freeze overlay reports neither, so a
// slow answer and a dead request look exactly alike from the form. Track the expected duration
// instead, hold short of full while the request is still open, and let only a real response
// finish the bar.
const LTL_QUOTE_EXPECTED_SECONDS = 45
const LTL_QUOTE_GATEWAY_LIMIT_SECONDS = 60

function ltl_quote_progress_percent(elapsed_seconds) {
	if (elapsed_seconds <= LTL_QUOTE_EXPECTED_SECONDS) {
		return (elapsed_seconds / LTL_QUOTE_EXPECTED_SECONDS) * 90
	}
	// Overrunning the estimate is normal, so keep creeping rather than stalling at 90, but never
	// promise a completion we have not actually seen.
	const overrun = (elapsed_seconds - LTL_QUOTE_EXPECTED_SECONDS) / LTL_QUOTE_EXPECTED_SECONDS
	return Math.min(97, 90 + overrun * 7)
}

function ltl_quote_progress_description(elapsed_seconds) {
	const seconds = Math.round(elapsed_seconds)
	if (elapsed_seconds >= LTL_QUOTE_GATEWAY_LIMIT_SECONDS) {
		return __('Still waiting at {0}s, past the {1}s gateway limit. This request may not return.', [
			seconds,
			LTL_QUOTE_GATEWAY_LIMIT_SECONDS,
		])
	}
	if (elapsed_seconds >= LTL_QUOTE_EXPECTED_SECONDS) {
		return __('Still waiting on carriers ({0}s)', [seconds])
	}
	return __('Contacting carriers ({0}s)', [seconds])
}

function start_ltl_quote_progress() {
	const title = __('Getting LTL Quotes')
	const started = Date.now()
	let closed = false

	const render = () => {
		const elapsed = (Date.now() - started) / 1000
		frappe.show_progress(title, ltl_quote_progress_percent(elapsed), 100, ltl_quote_progress_description(elapsed))
	}

	// Same title on every call, so frappe reuses the one dialog instead of stacking them.
	render()
	const timer = setInterval(render, 500)

	const stop = () => {
		if (closed) return false
		closed = true
		clearInterval(timer)
		return true
	}

	return {
		finish: () => {
			if (!stop()) return
			frappe.show_progress(title, 100, 100, __('Done'))
			setTimeout(() => frappe.hide_progress(), 500)
		},
		abandon: () => {
			const elapsed = (Date.now() - started) / 1000
			if (!stop()) return
			frappe.hide_progress()
			// A gateway timeout closes the connection without a response, so nothing else is
			// going to say anything. Quicker failures carry a server message that speaks for itself.
			if (elapsed >= LTL_QUOTE_GATEWAY_LIMIT_SECONDS) {
				frappe.msgprint({
					title: __('LTL quote request timed out'),
					indicator: 'orange',
					message: __(
						'The carriers did not answer within {0} seconds and the connection was closed. Nothing was saved, so this is safe to run again.',
						[LTL_QUOTE_GATEWAY_LIMIT_SECONDS]
					),
				})
			}
		},
	}
}

function show_ltl_quote_selection_dialog(frm, quotes) {
	const fmt_currency = (val, currency) => `${currency || 'USD'} ${parseFloat(val || 0).toFixed(2)}`
	const fmt_days = d => (d != null ? `${d} day${d !== 1 ? 's' : ''}` : '—')

	const rows = quotes
		.map(
			(q, i) => `
		<tr>
			<td class="text-center"><input type="checkbox" class="ltl-quote-check" data-idx="${i}"></td>
			<td>${frappe.utils.escape_html(q.carrier_name || '')}${q.carrier_scac ? ` <small class="text-muted">(${q.carrier_scac})</small>` : ''}</td>
			<td>${frappe.utils.escape_html(q.service_level || '—')}</td>
			<td class="text-center">${fmt_days(q.transit_days)}</td>
			<td class="text-right"><strong>${fmt_currency(q.total_price, q.currency)}</strong></td>
		</tr>`
		)
		.join('')

	const html = `
		<div style="margin-bottom:8px">
			<label><input type="checkbox" id="ltl-select-all"> <strong>${__('Select all')}</strong></label>
		</div>
		<table class="table table-bordered table-condensed" style="margin-bottom:0">
			<thead>
				<tr>
					<th style="width:36px"></th>
					<th>${__('Carrier')}</th>
					<th>${__('Service')}</th>
					<th class="text-center">${__('Transit')}</th>
					<th class="text-right">${__('Total')}</th>
				</tr>
			</thead>
			<tbody>${rows}</tbody>
		</table>`

	const d = new frappe.ui.Dialog({
		title: __(`${quotes.length} LTL Quote(s) Received — Select to Save`),
		size: 'extra-large',
		fields: [{ fieldtype: 'HTML', options: html }],
		primary_action_label: __('Save Selected'),
		primary_action() {
			const selected = []
			d.$body.find('.ltl-quote-check:checked').each(function () {
				selected.push(quotes[parseInt($(this).data('idx'))])
			})
			if (!selected.length) {
				frappe.msgprint(__('Please select at least one quote to save.'))
				return
			}
			frappe.call({
				method: 'shipstation_integration.shipstation_integration.overrides.shipment.save_selected_ltl_quotes',
				args: {
					shipment_name: frm.doc.name,
					selected_quotes: selected,
				},
				freeze: true,
				callback(r) {
					d.hide()
					if (r && r.message) frappe.msgprint(r.message)
				},
			})
		},
	})

	d.show()

	d.$body.find('#ltl-select-all').on('change', function () {
		d.$body.find('.ltl-quote-check').prop('checked', this.checked)
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
					if (r && r.message) {
						frappe.msgprint(__(r.message))
						frm.reload()
					}
				},
			})
		})
	} else {
		frm.remove_custom_button('Schedule LTL Pickup')
	}
}
