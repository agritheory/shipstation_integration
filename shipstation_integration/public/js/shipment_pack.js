// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

const SDN_PARCEL_COLORS = ['#2AC48A', '#5E64FF', '#FF8A00', '#A553E0', '#3478F6', '#D62B31']

function sdn_get_parcel_color(parcel_number) {
	if (!parcel_number) return null
	return SDN_PARCEL_COLORS[(parcel_number - 1) % SDN_PARCEL_COLORS.length]
}

function sdn_get_grid_bulk_actions(grid) {
	if (!grid?.wrapper) return $()
	let $bulk = grid.wrapper.find('.grid-bulk-actions')
	if (!$bulk.length) {
		const $flex = grid.wrapper.find('.grid-footer .flex')
		if ($flex.length) {
			$bulk = $('<div class="grid-bulk-actions text-right"></div>')
			$flex.append($bulk)
		}
	}
	return $bulk
}

function sdn_attach_tab_listener_for_parcel_buttons(frm) {
	if (frm.__sdn_parcel_tab_listener) return
	frm.__sdn_parcel_tab_listener = true
	frm.$wrapper.on('shown.bs.tab.sdn-parcel', '.form-tabs .nav-link', () => {
		setTimeout(() => sdn_setup_parcel_buttons(frm), 0)
		setTimeout(() => sdn_setup_parcel_buttons(frm), 120)
	})
}

function sdn_attach_grid_change_for_parcel_buttons(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid?.wrapper || grid.wrapper.data('sdn-parcel-grid-change')) return
	grid.wrapper.data('sdn-parcel-grid-change', true)
	grid.wrapper.on('change.sdn-parcel-btns', () => {
		setTimeout(() => sdn_setup_parcel_buttons(frm), 0)
	})
}

function sdn_apply_sdn_grid_parcel_header(frm, grid) {
	if (!grid?.header_row?.wrapper) return
	const $span = $(grid.header_row.wrapper).find('.row-index span')
	if (!$span.length) return
	if (frm._ss_cartonization_enabled) {
		$span.text(__('Parcel'))
	} else {
		$span.text('')
	}
}

function sdn_render_parcel_indicators(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return

	sdn_update_split_button_state(frm)
	sdn_apply_sdn_grid_parcel_header(frm, grid)

	const rows = frm.doc.shipment_delivery_note || []
	rows.forEach((row, i) => {
		const grid_row = grid.grid_rows[i]
		if (!grid_row) return
		const $row_index = $(grid_row.wrapper).find('.row-index')
		const $span = $row_index.find('span')
		$row_index.find('.parcel-indicator').remove()

		if (row.parcel_number) {
			const color = sdn_get_parcel_color(row.parcel_number)
			$span.hide()
			const title = __('Parcel {0}', [row.parcel_number])
			$(
				`<span class="parcel-indicator" title="${title}" style="display:inline-block;background:${color};color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;line-height:1.6;vertical-align:middle;cursor:default;">${row.parcel_number}</span>`
			).appendTo($row_index)
		} else {
			$span.hide()
		}
	})
}

function sdn_sync_cartonize_grid_button(frm, $bulk_actions) {
	if (!$bulk_actions?.length) return
	const $existing = $bulk_actions.find('.sdn-cartonize-rows')
	if (frm._ss_cartonization_enabled) {
		if ($existing.length) return
		const $unpack = $bulk_actions.find('.sdn-unpack-rows')
		if (!$unpack.length) return
		$('<button type="button" class="sdn-cartonize-rows btn btn-xs btn-default" style="margin-right:4px;">')
			.text(__('Cartonize'))
			.on('click', () => sdn_cartonize_rows(frm))
			.insertBefore($unpack)
	} else {
		$existing.remove()
	}
}

function sdn_setup_parcel_buttons(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid?.wrapper) return

	const $bulk_actions = sdn_get_grid_bulk_actions(grid)
	if (!$bulk_actions.length) return

	if ($bulk_actions.find('.sdn-pack-rows').length) {
		sdn_sync_cartonize_grid_button(frm, $bulk_actions)
		sdn_update_split_button_state(frm)
		return
	}

	if (!grid.wrapper.data('sdn-split-check-bound')) {
		grid.wrapper.data('sdn-split-check-bound', true)
		grid.wrapper.on('change.sdn-split-check', '.grid-row-check', () => {
			sdn_update_split_button_state(frm)
		})
	}

	$bulk_actions.prepend(
		$('<button type="button" class="sdn-unpack-rows btn btn-xs btn-warning">')
			.text(__('Unpack'))
			.on('click', () => sdn_unpack_selected_rows(frm))
	)
	$bulk_actions.prepend(
		$(
			'<button type="button" class="sdn-pack-each-row btn btn-xs" style="margin-right:4px;background-color:#3478F6;border-color:#3478F6;color:#fff;">'
		)
			.text(__('Pack Each Row'))
			.on('click', () => sdn_pack_each_row(frm))
	)
	$bulk_actions.prepend(
		$('<button type="button" class="sdn-pack-rows btn btn-xs btn-success" style="margin-right:4px;">')
			.text(__('Pack'))
			.on('click', () => sdn_pack_selected_rows(frm))
	)
	$bulk_actions.prepend(
		$(
			'<button type="button" class="sdn-split-rows btn btn-xs" style="margin-right:4px;background-color:var(--pink);border-color:var(--pink);color:#fff;" disabled>'
		)
			.text(__('Split'))
			.on('click', () => sdn_split_selected_rows(frm))
	)

	sdn_sync_cartonize_grid_button(frm, $bulk_actions)
	sdn_update_split_button_state(frm)
}

function sdn_deselect_all_rows(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return
	grid.grid_rows.forEach(row => {
		row.select(false)
		row.refresh_check()
	})
	grid.refresh_remove_rows_button()
	sdn_update_split_button_state(frm)
}

function sdn_update_split_button_state(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return
	const $btn = $(grid.wrapper).find('.sdn-split-rows')
	const selected = grid.get_selected_children()
	const can_split = selected.some(r => flt(r.qty) > 1)
	$btn.prop('disabled', !can_split)
}

function sdn_next_available_parcel(rows) {
	const used = new Set((rows || []).map(r => r.parcel_number).filter(n => n > 0))
	let n = 1
	while (used.has(n)) n++
	return n
}

function sdn_pack_selected_rows(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return false
	const selected = grid.get_selected_children()
	if (!selected.length) {
		frappe.msgprint(__('Please select at least one row to pack.'))
		return false
	}

	const next = sdn_next_available_parcel(frm.doc.shipment_delivery_note)
	const promises = selected.map(row => frappe.model.set_value(row.doctype, row.name, 'parcel_number', next))
	Promise.all(promises).then(() => {
		sdn_render_parcel_indicators(frm)
		sdn_deselect_all_rows(frm)
		frm.dirty()
	})
	return false
}

function sdn_pack_each_row(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return false
	const selected = grid.get_selected_children()
	if (!selected.length) {
		frappe.msgprint(__('Please select at least one row to pack.'))
		return false
	}

	const assignments = []
	selected.forEach(row => {
		const next = sdn_next_available_parcel(frm.doc.shipment_delivery_note)
		row.parcel_number = next
		assignments.push({ row, parcel_number: next })
	})

	const promises = assignments.map(({ row, parcel_number }) =>
		frappe.model.set_value(row.doctype, row.name, 'parcel_number', parcel_number)
	)
	Promise.all(promises).then(() => {
		sdn_render_parcel_indicators(frm)
		sdn_deselect_all_rows(frm)
		frm.dirty()
	})
	return false
}

function sdn_unpack_selected_rows(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return false
	const selected = grid.get_selected_children()
	if (!selected.length) {
		frappe.msgprint(__('Please select at least one row to unpack.'))
		return false
	}

	const promises = selected.map(row => frappe.model.set_value(row.doctype, row.name, 'parcel_number', 0))
	Promise.all(promises).then(() => {
		sdn_render_parcel_indicators(frm)
		sdn_deselect_all_rows(frm)
		frm.dirty()
	})
	return false
}

function sdn_cartonize_rows(frm) {
	if (!frm._ss_cartonization_enabled) {
		frappe.msgprint(__('Enable cartonization in Shipstation Settings to use this action.'))
		return false
	}
	if (!frappe.boot.inventory_tools_installed) {
		frappe.msgprint(__('Install Inventory Tools to use cartonization.'))
		return false
	}

	if (!frm.doc.name || frm.doc.__islocal) {
		frappe.msgprint(__('Save the Shipment before cartonizing.'))
		return false
	}

	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return false
	const selected = grid.get_selected_children()
	let rowNamesPayload = null
	if (selected.length) {
		rowNamesPayload = JSON.stringify(selected.map(r => r.name))
		if (!selected.some(r => !r.parcel_number)) {
			frappe.msgprint(__('Selected rows already have parcels. Unpack first or choose other rows.'))
			return false
		}
	} else if (!(frm.doc.shipment_delivery_note || []).some(r => !r.parcel_number && r.item_code)) {
		frappe.msgprint(__('Nothing to cartonize — all rows are already assigned.'))
		return false
	}

	frappe.call({
		method: 'shipstation_integration.cartonization.apply_cartonization_to_shipment',
		args: {
			shipment_name: frm.doc.name,
			row_names_json: rowNamesPayload,
		},
		freeze: true,
		callback(r) {
			const sol = r.message || {}
			frm.reload_doc().then(() => {
				sdn_render_parcel_indicators(frm)
				if ((sol.messages || []).length) {
					frappe.msgprint(sol.messages.join('<br>'))
				}
			})
		},
	})
	return false
}

function sdn_split_selected_rows(frm) {
	const grid = frm.fields_dict.shipment_delivery_note?.grid
	if (!grid) return false
	const selected = grid.get_selected_children()
	const splittable = selected.filter(r => flt(r.qty) > 1)
	if (!splittable.length) return false

	const copyable_fields = new Set(
		frappe
			.get_meta(splittable[0].doctype)
			.fields.filter(f => !f.no_copy)
			.map(f => f.fieldname)
	)

	splittable.forEach(row => {
		frappe.model.set_value(row.doctype, row.name, 'qty', flt(row.qty) - 1)
		const new_row = frappe.model.add_child(frm.doc, row.doctype, 'shipment_delivery_note')
		copyable_fields.forEach(fieldname => {
			new_row[fieldname] = row[fieldname]
		})
		// dn_detail is no_copy but both halves reference the same DN item
		new_row.dn_detail = row.dn_detail
		new_row.qty = 1
	})

	grid.refresh()
	sdn_render_parcel_indicators(frm)
	sdn_populate_all_parcel_details(frm)
	sdn_deselect_all_rows(frm)
	frm.dirty()
	return false
}

const SDN_DIM_UOM_ABBR = {
	Inch: '"',
	Centimeter: 'cm',
	Foot: "'",
	Millimeter: 'mm',
	Meter: 'm',
}

const SDN_WEIGHT_UOM_ABBR = {
	Pound: 'lbs',
	Kg: 'kg',
	Kilogram: 'kg',
	Ounce: 'oz',
	Gram: 'g',
}

function sdn_populate_all_parcel_details(frm) {
	;(frm.doc.shipment_delivery_note || []).forEach(row => {
		sdn_update_parcel_details(frm, row.doctype, row.name)
	})
}

function sdn_update_parcel_details(frm, cdt, cdn) {
	const row = locals[cdt][cdn]
	const l = row.parcel_length
	const w = row.parcel_width
	const h = row.parcel_height
	const wt = row.parcel_weight

	if (!l && !w && !h && !wt) {
		frappe.model.set_value(cdt, cdn, 'parcel_details', '')
		return
	}

	const dim_abbr = SDN_DIM_UOM_ABBR[row.dimension_uom] || row.dimension_uom || ''
	const wt_abbr = SDN_WEIGHT_UOM_ABBR[row.parcel_weight_uom] || row.parcel_weight_uom || ''

	const parts = []
	if (l || w || h) {
		const fmt = v => (v % 1 === 0 ? v : flt(v, 2))
		parts.push(`${fmt(l || 0)}x${fmt(w || 0)}x${fmt(h || 0)}${dim_abbr}`)
	}
	if (wt) {
		parts.push(`${flt(wt, 2)}${wt_abbr}`)
	}

	frappe.model.set_value(cdt, cdn, 'parcel_details', parts.join(' '))
}

function sdn_check_template_match(frm, cdt, cdn) {
	const row = locals[cdt][cdn]
	if (!row.parcel_length || !row.parcel_width || !row.parcel_height) return
	if (row.parcel_template) return

	frappe.call({
		method: 'shipstation_integration.utils.find_matching_parcel_template',
		args: {
			length: row.parcel_length,
			width: row.parcel_width,
			height: row.parcel_height,
			dimension_uom: row.dimension_uom,
		},
		callback: function (r) {
			if (r.message) {
				frappe.model.set_value(cdt, cdn, 'parcel_template', r.message)
				frm.refresh_field('shipment_delivery_note')
			}
		},
	})
}

function sdn_try_resolve_dn_item_link(_frm, cdt, cdn) {
	const row = locals[cdt][cdn]
	if (!row || !row.delivery_note || row.dn_detail) {
		return
	}

	frappe.db
		.get_list('Delivery Note Item', {
			filters: { parent: row.delivery_note },
			fields: ['name', 'item_code', 'item_name', 'qty', 'stock_uom', 'base_amount', 'amount'],
			order_by: 'idx asc',
			limit: 500,
		})
		.then(items => {
			if (!items || !items.length) return

			let match = null
			if (items.length === 1) {
				match = items[0]
			} else if (row.item_code) {
				const by_code = items.filter(i => i.item_code === row.item_code)
				if (by_code.length === 1) match = by_code[0]
				else if (row.qty && by_code.length > 1) {
					const qhits = by_code.filter(i => flt(i.qty) === flt(row.qty))
					if (qhits.length === 1) match = qhits[0]
				}
			}
			if (!match) return

			frappe.model.set_value(cdt, cdn, {
				dn_detail: match.name,
				item_code: match.item_code,
				item_name: match.item_name,
				qty: match.qty,
				stock_uom: match.stock_uom,
				grand_total: flt(match.base_amount || match.amount),
			})
		})
}

function fetch_delivery_note_items(frm) {
	const existing_dn_details = new Set((frm.doc.shipment_delivery_note || []).map(r => r.dn_detail).filter(Boolean))

	// Build setter list — pre-fill and lock customer when one is set on the Shipment
	const setters = []
	if (frm.doc.delivery_to_type === 'Customer' && frm.doc.delivery_customer) {
		setters.push({
			label: __('Customer'),
			fieldname: 'customer',
			fieldtype: 'Link',
			options: 'Customer',
			default: frm.doc.delivery_customer,
			read_only: 1,
		})
	}
	// no customer pre-filter when using ad-hoc delivery contact — leave picker open

	const d = new frappe.ui.form.MultiSelectDialog({
		doctype: 'Delivery Note',
		target: frm,
		setters: setters,
		get_query: () => ({
			filters: { status: ['not in', ['Cancelled', 'Return']] },
		}),
		add_filters_group: 1,
		allow_child_item_selection: true,
		child_fieldname: 'items',
		child_columns: ['item_code', 'item_name', 'qty', 'stock_uom'],
		action: function (selections, args) {
			if (!selections.length) {
				frappe.msgprint(__('Please select at least one Delivery Note.'))
				return
			}
			d.dialog.hide()

			const filtered_children = (args && args.filtered_children) || []
			const fetch_filter = filtered_children.length
				? [['name', 'in', filtered_children]]
				: [['parent', 'in', selections]]

			frappe.call({
				method: 'frappe.client.get_list',
				args: {
					doctype: 'Delivery Note Item',
					parent: 'Delivery Note',
					filters: fetch_filter,
					fields: ['name', 'parent', 'item_code', 'item_name', 'qty', 'stock_uom', 'base_amount', 'amount'],
					limit_page_length: 500,
				},
				callback: function (r) {
					if (!r.message || !r.message.length) return
					let added = 0
					r.message.forEach(item => {
						if (existing_dn_details.has(item.name)) return
						existing_dn_details.add(item.name)
						const new_row = frappe.model.add_child(frm.doc, 'Shipment Delivery Note', 'shipment_delivery_note')
						new_row.delivery_note = item.parent
						new_row.dn_detail = item.name
						new_row.item_code = item.item_code
						new_row.item_name = item.item_name
						new_row.qty = item.qty
						new_row.stock_uom = item.stock_uom
						new_row.grand_total = flt(item.base_amount || item.amount)
						added++
					})
					frm.refresh_field('shipment_delivery_note')
					sdn_render_parcel_indicators(frm)
					if (added) {
						frappe.show_alert({ message: __('Added {0} item(s) from Delivery Notes', [added]), indicator: 'green' }, 5)
						frm.dirty()
					}
				},
			})
		},
	})
}

function sdn_setup_sscc_button(frm) {
	if (!(frm.doc.shipment_delivery_note || []).some(r => r.parcel_number)) return

	frappe.call({
		method: 'frappe.client.get_value',
		args: {
			doctype: 'Shipstation Settings',
			filters: { enabled: 1 },
			fieldname: 'gs1_company_prefix',
		},
		callback: function (r) {
			if (!r.message || !r.message.gs1_company_prefix) return
			frm.add_custom_button(__('Generate SSCC'), () => sdn_generate_sscc(frm), __('Pack'))
		},
	})
}

function sdn_generate_sscc(frm) {
	frappe.call({
		method: 'shipstation_integration.sscc.generate_shipment_sscc',
		args: { shipment: frm.doc.name },
		freeze: true,
		freeze_message: __('Generating SSCC codes...'),
		callback: function (r) {
			if (!r.message) return
			const { generated, skipped, codes } = r.message

			if (codes && codes.length) {
				codes.forEach(({ name, ucc128 }) => {
					frappe.model.set_value('Shipment Delivery Note', name, 'ucc128', ucc128)
				})
				frm.refresh_field('shipment_delivery_note')
				frm.dirty()
			}

			if (generated) {
				frappe.show_alert({ message: __('Generated {0} SSCC code(s)', [generated]), indicator: 'green' }, 5)
			}
			if (skipped) {
				frappe.show_alert(
					{ message: __('{0} parcel(s) already had an SSCC — skipped', [skipped]), indicator: 'orange' },
					5
				)
			}
			if (!generated && !skipped) {
				frappe.show_alert({ message: __('No SSCC changes made'), indicator: 'gray' }, 4)
			}
		},
		error: function (err) {
			frappe.msgprint(__('Error generating SSCC: {0}', [err.message || 'Unknown error']))
		},
	})
}

function sdn_setup_shipping_actions(frm) {
	if (frm.doc.freight_type !== 'LTL' && frm.doc.freight_type !== 'Small Parcel') return
	if (frm.doc.freight_type === 'LTL') return // LTL uses quotation workflow

	frappe.call({
		method: 'frappe.client.get_value',
		args: {
			doctype: 'Shipstation Settings',
			filters: { enabled: 1 },
			fieldname: ['name', 'enable_shipstation_api'],
		},
		callback: function (r) {
			if (!r.message || !r.message.enable_shipstation_api) return

			const has_tracking = (frm.doc.shipment_delivery_note || []).some(r => r.tracking_number)
			if (!has_tracking) {
				frm.add_custom_button(__('Compare Rates'), () => sdn_get_shipping_rates(frm), __('Pack'))
				frm.add_custom_button(__('Create Label'), () => sdn_create_label(frm), __('Pack'))
			}
		},
	})
}

function sdn_get_shipping_rates(frm) {
	const has_parcel = (frm.doc.shipment_delivery_note || []).some(r => r.parcel_number)
	if (!has_parcel) {
		frappe.msgprint(__('Please assign items to parcels before comparing rates.'))
		return
	}

	frappe.call({
		method: 'shipstation_integration.rates.get_rates_for_shipment',
		args: { shipment: frm.doc.name },
		freeze: true,
		freeze_message: __('Fetching shipping rates...'),
		callback: function (r) {
			if (r.message && r.message.length) {
				sdn_show_rates_dialog(frm, r.message)
			} else {
				frappe.msgprint(__('No rates returned. Please check carrier configuration.'))
			}
		},
		error: function (err) {
			frappe.msgprint(__('Error fetching rates: {0}', [err.message || 'Unknown error']))
		},
	})
}

function sdn_show_rates_dialog(frm, rates) {
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
				options: sdn_build_rates_html(rates),
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
			sdn_create_label_with_rate(frm, selected.carrier_id, selected.service_code)
		},
	})

	dialog.show()
}

function sdn_build_rates_html(rates) {
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

function sdn_create_label(frm) {
	const carrier = frm.doc.preferred_carrier
	if (!carrier) {
		frappe.msgprint(__('Please select a Preferred Carrier first.'))
		return
	}

	frappe.call({
		method: 'shipstation_integration.carriers.get_carrier_id_for_supplier',
		args: { supplier_name: carrier },
		callback: function (r) {
			if (!r.message) {
				frappe.msgprint(__('Could not find ShipEngine carrier for {0}.', [carrier]))
				return
			}
			sdn_create_label_with_rate(frm, r.message, null)
		},
	})
}

function sdn_create_label_with_rate(frm, carrier_id, service_code) {
	frappe.call({
		method: 'shipstation_integration.labels.create_label_for_shipment',
		args: {
			shipment: frm.doc.name,
			carrier_id,
			service_code,
		},
		freeze: true,
		freeze_message: __('Creating shipping label...'),
		callback: function (r) {
			if (r.message) sdn_show_label_success(frm, r.message)
		},
		error: function (err) {
			frappe.msgprint(__('Error creating label: {0}', [err.message || 'Unknown error']))
		},
	})
}

function sdn_show_label_success(frm, results) {
	const list = Array.isArray(results) ? results : [results]
	list.forEach(result => {
		const parcel = result.parcel_number ? __('Parcel {0}', [result.parcel_number]) : __('Label')
		const tracking = result.tracking_number || ''
		const msg = tracking ? __(`{0} created — {1}`, [parcel, tracking]) : __(`{0} created`, [parcel])
		frappe.show_alert({ message: msg, indicator: 'green' }, 7)

		if (!result.parcel_number) return
		;(frm.doc.shipment_delivery_note || []).forEach(row => {
			if (row.parcel_number !== result.parcel_number) return
			frappe.model.set_value(row.doctype, row.name, {
				tracking_number: result.tracking_number || '',
				label_url: result.label_download || '',
			})
		})
	})
	frm.refresh_field('shipment_delivery_note')
}

function sdn_run_pack_refresh_workflow(frm) {
	sdn_attach_tab_listener_for_parcel_buttons(frm)
	sdn_attach_grid_change_for_parcel_buttons(frm)
	sdn_setup_parcel_buttons(frm)
	setTimeout(() => sdn_setup_parcel_buttons(frm), 0)
	setTimeout(() => sdn_setup_parcel_buttons(frm), 150)
	sdn_render_parcel_indicators(frm)
	sdn_populate_all_parcel_details(frm)
	sdn_setup_sscc_button(frm)
	sdn_setup_shipping_actions(frm)

	frm.add_custom_button(__('Delivery Note'), () => fetch_delivery_note_items(frm), __('Get Items From'))
}

frappe.ui.form.on('Shipment', {
	setup(frm) {
		frm._ss_cartonization_enabled = false
	},

	delivery_to_type: function (frm) {
		frm.refresh_field('delivery_contact_name')
	},
	delivery_customer: function (frm) {
		if (frm.doc.delivery_to_type === 'Customer') {
			frm.refresh_field('delivery_contact_name')
		}
	},
	refresh: function (frm) {
		// Override ERPNext's base set_query (registered in its setup handler) which
		// restricts delivery_note to docstatus == 1. Shipments are planned before
		// the DN is submitted — that filter is backwards for this workflow.
		// Placing this in refresh (which fires after all setup handlers) guarantees
		// our version wins regardless of app load order.
		frm.set_query('delivery_note', 'shipment_delivery_note', function () {
			const filters = { status: ['not in', ['Cancelled']] }
			if (frm.doc.delivery_to_type === 'Customer' && frm.doc.delivery_customer) {
				filters.customer = frm.doc.delivery_customer
			} else if (frm.doc.delivery_to_type === 'Company' && frm.doc.delivery_company) {
				filters.customer = frm.doc.delivery_company
			}
			// no filter when neither party is set (ad-hoc delivery contact)
			return { filters }
		})

		frm.set_query('delivery_contact_name', function () {
			const filters = {}
			if (frm.doc.delivery_to_type === 'Customer' && frm.doc.delivery_customer) {
				filters['link_doctype'] = 'Customer'
				filters['link_name'] = frm.doc.delivery_customer
			}
			return { filters }
		})

		frm.set_query('dn_detail', 'shipment_delivery_note', function (doc, cdt, cdn) {
			const row = locals[cdt][cdn]
			return row?.delivery_note
				? { filters: { parent: row.delivery_note } }
				: { filters: [['name', '=', '__no_dn_linked__']] }
		})

		frappe.call({
			method: 'shipstation_integration.cartonization.is_cartonization_enabled',
			callback(r) {
				frm._ss_cartonization_enabled = !!r.message
				sdn_run_pack_refresh_workflow(frm)
			},
		})
	},

	parcel_template: function (frm) {
		const template_name = frm.doc.parcel_template
		if (!template_name || !(frm.doc.shipment_delivery_note || []).length) return

		frappe.db.get_doc('Shipment Parcel Template', template_name).then(template => {
			if (!template) return

			const updates = frm.doc.shipment_delivery_note.map(row =>
				frappe.model.set_value(row.doctype, row.name, {
					parcel_template: template_name,
					parcel_length: template.length,
					parcel_width: template.width,
					parcel_height: template.height,
					dimension_uom: 'Centimeter',
					parcel_weight: template.weight || 0,
					parcel_weight_uom: 'Kg',
				})
			)

			Promise.all(updates).then(() => {
				frm.refresh_field('shipment_delivery_note')
				;(frm.doc.shipment_delivery_note || []).forEach(row => sdn_update_parcel_details(frm, row.doctype, row.name))
				frm.dirty()
			})
		})
	},

	shipment_delivery_note_add: function (frm) {
		sdn_render_parcel_indicators(frm)
		setTimeout(() => sdn_setup_parcel_buttons(frm), 0)
	},

	shipment_delivery_note_remove: function (frm) {
		sdn_render_parcel_indicators(frm)
		setTimeout(() => sdn_setup_parcel_buttons(frm), 0)
	},

	shipment_delivery_note_move: function (frm) {
		frappe.msgprint(__('Moving rows may require re-packing existing parcels.'), __('Warning'))
		sdn_render_parcel_indicators(frm)
		setTimeout(() => sdn_setup_parcel_buttons(frm), 0)
	},
})

frappe.ui.form.on('Shipment Delivery Note', {
	delivery_note: function (frm, cdt, cdn) {
		sdn_try_resolve_dn_item_link(frm, cdt, cdn)
	},

	item_code: function (frm, cdt, cdn) {
		sdn_try_resolve_dn_item_link(frm, cdt, cdn)
	},

	qty: function (frm, cdt, cdn) {
		sdn_try_resolve_dn_item_link(frm, cdt, cdn)
	},

	parcel_number: function (frm) {
		sdn_render_parcel_indicators(frm)
	},

	parcel_template: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (!row.parcel_template) return

		frappe.db.get_doc('Shipment Parcel Template', row.parcel_template).then(template => {
			if (!template) return
			frappe.model.set_value(cdt, cdn, {
				parcel_length: template.length,
				parcel_width: template.width,
				parcel_height: template.height,
				dimension_uom: 'Centimeter',
				parcel_weight: template.weight || 0,
				parcel_weight_uom: 'Kg',
			})
			frm.refresh_field('shipment_delivery_note')
			sdn_update_parcel_details(frm, cdt, cdn)
		})
	},

	parcel_length: function (frm, cdt, cdn) {
		sdn_check_template_match(frm, cdt, cdn)
		sdn_update_parcel_details(frm, cdt, cdn)
	},

	parcel_width: function (frm, cdt, cdn) {
		sdn_check_template_match(frm, cdt, cdn)
		sdn_update_parcel_details(frm, cdt, cdn)
	},

	parcel_height: function (frm, cdt, cdn) {
		sdn_check_template_match(frm, cdt, cdn)
		sdn_update_parcel_details(frm, cdt, cdn)
	},

	dimension_uom: function (frm, cdt, cdn) {
		sdn_update_parcel_details(frm, cdt, cdn)
	},

	parcel_weight: function (frm, cdt, cdn) {
		sdn_update_parcel_details(frm, cdt, cdn)
	},

	parcel_weight_uom: function (frm, cdt, cdn) {
		sdn_update_parcel_details(frm, cdt, cdn)
	},
})
