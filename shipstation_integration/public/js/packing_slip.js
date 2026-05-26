// Copyright (c) 2024, AgriTheory and contributors
// For license information, please see license.txt

const PARCEL_COLORS = ['#2AC48A', '#5E64FF', '#FF8A00', '#A553E0', '#3478F6', '#D62B31']

const PACKING_SLIP_PURPLE = '#6f42c1'

function get_parcel_color(parcel_number) {
	if (!parcel_number) return null
	return PARCEL_COLORS[(parcel_number - 1) % PARCEL_COLORS.length]
}

function is_row_cartonized(row) {
	return flt(row?.parcel_number) > 0
}

function parcel_header_package_icon(opts) {
	const opacity = opts && opts.opacity != null ? String(opts.opacity) : '1'
	const stroke = opts && opts.strokeColor
	let style = 'font-size:14px;line-height:1;vertical-align:middle;'
	if (stroke) {
		style += `color:${stroke};`
	} else {
		style += `opacity:${opacity};`
	}
	return `<i class="octicon octicon-package" aria-hidden="true" style="${style}"></i>`
}

/** True only for the numbered-column header cell (inside heading row, not body or filter row). */
function is_heading_row_index_cell($cell) {
	if (!$cell?.length || !$cell[0]?.closest) return false
	const el = $cell[0]
	return !!el.closest('.grid-heading-row') && !el.closest('.grid-body')
}

/** Parcel / No. column cell in the grid *heading* only (not body rows). */
function items_grid_heading_row_index_cell(grid) {
	if (!grid?.wrapper?.length) return $()

	if (grid.header_row?.row_index?.length && is_heading_row_index_cell($(grid.header_row.row_index))) {
		return grid.header_row.row_index.first()
	}

	const $gh = grid.wrapper.children('.form-grid-container').children('.form-grid').children('.grid-heading-row').first()
	if (!$gh.length) return $()

	// Same order as Grid.make_head: label row grid-row first, filter row second.
	let $hdr = $gh
		.children('.grid-row')
		.first()
		.children('.data-row:not(.filter-row)')
		.first()
		.children('.row-index:not(.search)')
		.first()
	if (is_heading_row_index_cell($hdr)) return $hdr

	// Fallback: any row-index directly under heading (defensive against DOM tweaks).
	const $fallback = $gh.find('> .grid-row > .data-row:not(.filter-row) > .row-index:not(.search)').first()
	return is_heading_row_index_cell($fallback) ? $fallback : $()
}

/** Remove parcel header widgets accidentally injected into body rows (legacy bug / race). */
function strip_leaked_parcel_header_widgets_from_grid_body(grid) {
	if (!grid?.wrapper?.length) return
	const sel =
		'.parcel-header-toolbar, .parcel-toggle-uncartonized, .parcel-toggle-cartonized, .parcel-header-parcel-block'
	grid.wrapper.find('.grid-body .row-index').find(sel).remove()
	grid.wrapper.find('.grid-body .row-index span.parcel-col-label').removeClass('parcel-col-label').empty()
}

function toggle_uncartonized_row_selection(frm) {
	const grid = frm.fields_dict.items.grid
	const items = frm.doc.items || []
	const indices = []
	items.forEach((row, i) => {
		if (!is_row_cartonized(row)) indices.push(i)
	})
	if (!indices.length) {
		frappe.show_alert({
			message: __('No unpacked rows on this Packing Slip.'),
			indicator: 'orange',
		})
		return
	}
	const allSel = indices.every(i => !!(items[i] && items[i].__checked))
	const nextChecked = !allSel
	for (const i of indices) {
		const gr = grid.grid_rows[i]
		const row = items[i]
		if (!gr?.doc || !row) continue
		gr.select(nextChecked)
		gr.refresh_check()
	}
	grid.refresh_remove_rows_button()
	update_split_button_state(frm)
}

function toggle_cartonized_row_selection(frm) {
	const grid = frm.fields_dict.items.grid
	const items = frm.doc.items || []
	const indices = []
	items.forEach((row, i) => {
		if (is_row_cartonized(row)) indices.push(i)
	})
	if (!indices.length) {
		frappe.show_alert({
			message: __('No cartonized rows on this Packing Slip.'),
			indicator: 'orange',
		})
		return
	}
	const allSel = indices.every(i => !!(items[i] && items[i].__checked))
	const nextChecked = !allSel
	for (const i of indices) {
		const gr = grid.grid_rows[i]
		const row = items[i]
		if (!gr?.doc || !row) continue
		gr.select(nextChecked)
		gr.refresh_check()
	}
	grid.refresh_remove_rows_button()
	update_split_button_state(frm)
}

function attach_parcel_column_header_handlers(frm) {
	const wrapper = frm.fields_dict.items?.grid?.wrapper
	if (!wrapper?.length || !frm._ss_cartonization_enabled) return
	$(wrapper)
		.off('click.ss-parcel-header keydown.ss-parcel-header')
		.on('click.ss-parcel-header', '.parcel-toggle-uncartonized', ev => {
			ev.preventDefault()
			ev.stopPropagation()
			toggle_uncartonized_row_selection(frm)
		})
		.on('click.ss-parcel-header', '.parcel-toggle-cartonized', ev => {
			ev.preventDefault()
			ev.stopPropagation()
			toggle_cartonized_row_selection(frm)
		})
		.on('keydown.ss-parcel-header', '.parcel-toggle-uncartonized, .parcel-toggle-cartonized', ev => {
			if (ev.key !== 'Enter' && ev.key !== ' ') return
			ev.preventDefault()
			ev.stopPropagation()
			if ($(ev.currentTarget).hasClass('parcel-toggle-cartonized')) {
				toggle_cartonized_row_selection(frm)
			} else {
				toggle_uncartonized_row_selection(frm)
			}
		})
}

function parcel_header_toggle_icon(attrs) {
	return $('<span></span>')
		.addClass(attrs.className)
		.attr('role', 'button')
		.attr('tabindex', 0)
		.attr('title', attrs.title)
		.css({
			cursor: 'pointer',
			display: 'inline-block',
			lineHeight: 1,
			padding: '0 3px',
			verticalAlign: 'middle',
		})
		.html(attrs.html)
}

function refresh_parcel_column_header_when_ready(frm, grid) {
	function try_parcel_hdr() {
		strip_leaked_parcel_header_widgets_from_grid_body(grid)
		const $hdr = items_grid_heading_row_index_cell(grid)
		if (!$hdr.length) return false
		refresh_parcel_column_header(frm, grid)
		return true
	}

	if (!try_parcel_hdr()) {
		queueMicrotask(() => try_parcel_hdr())
	}
}

function refresh_parcel_column_header(frm, grid) {
	const $hdr = items_grid_heading_row_index_cell(grid)
	if (!$hdr.length) return

	$hdr.find('.parcel-header-parcel-block').remove()
	$hdr.find('.parcel-header-toolbar').remove()
	$hdr.find('.parcel-toggle-uncartonized, .parcel-toggle-cartonized').remove()
	$hdr.find('i[class*="octicon"]').remove()

	let $lbl = $hdr.children('span.parcel-col-label').first()
	if (!$lbl.length) {
		const $first = $hdr.children('span').first()
		if ($first.length) {
			$first.addClass('parcel-col-label')
			$lbl = $first
		} else {
			$lbl = $('<span class="parcel-col-label"></span>')
			$hdr.prepend($lbl)
		}
	}
	// Screen readers only: avoid a second visible "Parcel" next to Parcel Template columns.
	$lbl.text(__('Parcel')).css({
		position: 'absolute',
		width: '1px',
		height: '1px',
		padding: 0,
		margin: '-1px',
		overflow: 'hidden',
		clip: 'rect(0,0,0,0)',
		whiteSpace: 'nowrap',
		border: 0,
	})

	const $toolbar = $('<span class="parcel-header-toolbar"></span>')
	$toolbar.attr('style', 'display:inline;white-space:nowrap;vertical-align:middle;margin-left:0;line-height:1;')

	const iconUnc = parcel_header_toggle_icon({
		className: 'parcel-toggle-uncartonized',
		title: __('Select all unpacked rows, or clear selection if already all selected'),
		html: parcel_header_package_icon({ opacity: 0.55 }),
	})
	const iconCart = parcel_header_toggle_icon({
		className: 'parcel-toggle-cartonized',
		title: __('Select all cartonized rows, or clear selection if already all selected'),
		html: parcel_header_package_icon({ strokeColor: PACKING_SLIP_PURPLE }),
	})

	$toolbar.append(iconUnc).append(iconCart)
	$hdr.append($toolbar)
}

function clear_parcel_column_header(frm, grid) {
	if (!grid?.wrapper?.length) return
	grid.wrapper.off('click.ss-parcel-header keydown.ss-parcel-header')
	strip_leaked_parcel_header_widgets_from_grid_body(grid)
	const $hdr = items_grid_heading_row_index_cell(grid)
	if (!$hdr.length) return
	$hdr.find('.parcel-header-parcel-block').remove()
	$hdr.find('.parcel-header-toolbar').remove()
	$hdr.find('.parcel-toggle-uncartonized, .parcel-toggle-cartonized').remove()
	$hdr.find('i[class*="octicon"]').remove()
	const $lbl = $hdr.children('span.parcel-col-label').first()
	if ($lbl.length) {
		$lbl.removeClass('parcel-col-label').empty().removeAttr('style')
	}
}

function render_parcel_indicators(frm) {
	const grid = frm.fields_dict.items.grid

	update_split_button_state(frm)

	if (frm._ss_cartonization_enabled) {
		refresh_parcel_column_header_when_ready(frm, grid)
		attach_parcel_column_header_handlers(frm)
	} else {
		clear_parcel_column_header(frm, grid)
	}

	const source_map = frm._source_hu_map || {}
	const items = frm.doc.items || []
	items.forEach((item, i) => {
		const grid_row = grid.grid_rows[i]
		if (!grid_row) return
		const $row_index = $(grid_row.wrapper).find('.row-index')
		$row_index
			.find(
				'.parcel-header-parcel-block, .parcel-header-toolbar, .parcel-toggle-uncartonized, .parcel-toggle-cartonized'
			)
			.remove()
		const $span = $row_index.find('span')
		$row_index.find('.parcel-indicator').remove()

		if (item.parcel_number) {
			const color = get_parcel_color(item.parcel_number)
			$span.hide()

			const source_hu = source_map[item.name]
			const title = source_hu
				? __('Parcel {0} — consuming HU {1}', [item.parcel_number, source_hu])
				: __('Parcel {0}', [item.parcel_number])
			const dot = source_hu
				? ` <span style="display:inline-block;width:6px;height:6px;border-radius:50%;background:rgba(255,255,255,0.7);vertical-align:middle;margin-left:2px;" title="${title}"></span>`
				: ''

			$(
				`<span class="parcel-indicator" title="${title}" style="display:inline-block;background:${color};color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;line-height:1.6;vertical-align:middle;cursor:default;">${item.parcel_number}${dot}</span>`
			).appendTo($row_index)
		} else {
			$span.hide()
		}
	})
}

function fetch_source_handling_units(frm) {
	if (!frm._ss_cartonization_enabled || !frm.doc.name || frm.doc.__islocal) return
	frappe.call({
		method: 'shipstation_integration.beam_integration.get_source_handling_units',
		args: { packing_slip: frm.doc.name },
		callback: function (r) {
			if (r.message && Object.keys(r.message).length) {
				frm._source_hu_map = r.message
				render_parcel_indicators(frm)
			}
		},
	})
}

function sync_cartonize_grid_button(frm, $bulk_actions) {
	if (!$bulk_actions?.length) return
	const $existing = $bulk_actions.find('.grid-cartonize-rows')
	if (frm._ss_cartonization_enabled) {
		if ($existing.length) return
		const $unpack = $bulk_actions.find('.grid-unpack-rows')
		if (!$unpack.length) return
		$('<button type="button" class="grid-cartonize-rows btn btn-xs btn-purple" style="margin-right:4px;">')
			.text(__('Cartonize'))
			.on('click', () => cartonize_packing_slip_rows(frm))
			.insertBefore($unpack)
	} else {
		$existing.remove()
	}
}

function setup_parcel_buttons(frm) {
	const grid = frm.fields_dict.items?.grid
	if (!grid?.wrapper?.length) return
	const $bulk_actions = $(grid.wrapper).find('.grid-bulk-actions')
	if (!$bulk_actions.length) return

	if (!$bulk_actions.find('.grid-pack-rows').length) {
		$bulk_actions.prepend(
			$('<button type="button" class="grid-unpack-rows btn btn-xs btn-warning">')
				.text(__('Unpack'))
				.on('click', () => unpack_selected_rows(frm))
		)
		$bulk_actions.prepend(
			$(
				'<button type="button" class="grid-pack-each-row btn btn-xs" style="margin-right:4px;background-color:#3478F6;border-color:#3478F6;color:#fff;">'
			)
				.text(__('Pack Each Row'))
				.on('click', () => pack_each_row(frm))
		)
		$bulk_actions.prepend(
			$('<button type="button" class="grid-pack-rows btn btn-xs btn-success" style="margin-right:4px;">')
				.text(__('Pack'))
				.on('click', () => pack_selected_rows(frm))
		)
		$bulk_actions.prepend(
			$(
				'<button type="button" class="grid-split-rows btn btn-xs" style="margin-right:4px;background-color:var(--pink);border-color:var(--pink);color:#fff;" disabled>'
			)
				.text(__('Split'))
				.on('click', () => split_selected_rows(frm))
		)

		$(grid.wrapper)
			.off('change.ss-split', '.grid-row-check')
			.on('change.ss-split', '.grid-row-check', () => {
				update_split_button_state(frm)
			})
	}

	sync_cartonize_grid_button(frm, $bulk_actions)
}

function deselect_all_rows(frm) {
	const grid = frm.fields_dict.items.grid
	grid.grid_rows.forEach(row => {
		row.select(false)
		row.refresh_check()
	})
	grid.refresh_remove_rows_button()
	update_split_button_state(frm)
}

function update_split_button_state(frm) {
	const $btn = $(frm.fields_dict.items.grid.wrapper).find('.grid-split-rows')
	const selected = frm.fields_dict.items.grid.get_selected_children()
	const can_split = selected.some(r => flt(r.qty) > 1)
	$btn.prop('disabled', !can_split)
}

function fetch_delivery_note_defaults(frm) {
	if (!frm.doc.delivery_note) return
	if (frm.__delivery_note_loaded) return
	frm.__delivery_note_loaded = true

	frappe.call({
		method: 'frappe.client.get',
		args: { doctype: 'Delivery Note', name: frm.doc.delivery_note },
		callback: function (r) {
			if (!r.message) return
			const dn = r.message

			frm.doc.__onload = frm.doc.__onload || {}
			frm.doc.__onload.customer = dn.customer

			if (!frm.doc.shipping_address_name && dn.shipping_address_name) {
				frm.set_value('shipping_address_name', dn.shipping_address_name)
			}

			// Prefer dispatch_address_name (ship-from) over company_address (billing)
			const dn_dispatch_addr = dn.dispatch_address_name || dn.company_address
			if (!frm.doc.dispatch_address_name && dn_dispatch_addr) {
				frm.set_value('dispatch_address_name', dn_dispatch_addr)
			}

			fetch_weight_from_delivery_note(frm, dn)

			if (!frm.doc.carrier) {
				frappe.call({
					method: 'shipstation_integration.carriers.get_shipping_accounts',
					args: { delivery_note: frm.doc.delivery_note },
					callback: function (r) {
						const accounts = r.message || []
						if (!accounts.length) return
						const account = accounts.find(a => a.default) || accounts.find(a => a.enabled) || accounts[0]
						if (account && account.carrier && !frm.doc.carrier) {
							frm.set_value('carrier', account.carrier)
						}
					},
				})
			}
		},
	})
}

function next_available_parcel(items) {
	const used = new Set((items || []).map(r => r.parcel_number).filter(n => n > 0))
	let n = 1
	while (used.has(n)) n++
	return n
}

function pack_selected_rows(frm) {
	const selected = frm.fields_dict.items.grid.get_selected_children()
	if (!selected.length) {
		frappe.msgprint(__('Please select at least one row to pack.'))
		return false
	}

	const next = next_available_parcel(frm.doc.items)

	const promises = selected.map(row => frappe.model.set_value(row.doctype, row.name, 'parcel_number', next))

	Promise.all(promises).then(() => {
		render_parcel_indicators(frm)
		deselect_all_rows(frm)
		frm.dirty()
	})

	return false
}

function pack_each_row(frm) {
	const selected = frm.fields_dict.items.grid.get_selected_children()
	if (!selected.length) {
		frappe.msgprint(__('Please select at least one row to pack.'))
		return false
	}

	// Pre-assign unique parcel numbers synchronously so each call to
	// next_available_parcel sees the previous in-memory assignment.
	const assignments = []
	selected.forEach(row => {
		const next = next_available_parcel(frm.doc.items)
		row.parcel_number = next
		assignments.push({ row, parcel_number: next })
	})

	const promises = assignments.map(({ row, parcel_number }) =>
		frappe.model.set_value(row.doctype, row.name, 'parcel_number', parcel_number)
	)

	Promise.all(promises).then(() => {
		render_parcel_indicators(frm)
		deselect_all_rows(frm)
		frm.dirty()
	})

	return false
}

function unpack_selected_rows(frm) {
	const selected = frm.fields_dict.items.grid.get_selected_children()
	if (!selected.length) {
		frappe.msgprint(__('Please select at least one row to unpack.'))
		return false
	}

	const promises = selected.map(row => frappe.model.set_value(row.doctype, row.name, 'parcel_number', 0))

	Promise.all(promises).then(() => {
		render_parcel_indicators(frm)
		deselect_all_rows(frm)
		frm.dirty()
	})

	return false
}

function cartonize_packing_slip_rows(frm) {
	if (!frm._ss_cartonization_enabled) {
		frappe.msgprint(__('Enable cartonization in Shipstation Settings to use this action.'))
		return false
	}
	if (!frappe.boot.inventory_tools_installed) {
		frappe.msgprint(__('Install Inventory Tools to use cartonization.'))
		return false
	}

	if (!frm.doc.name || frm.doc.__islocal) {
		frappe.msgprint(__('Save the Packing Slip before cartonizing.'))
		return false
	}

	const selected = frm.fields_dict.items.grid.get_selected_children()
	// targetRows is always only the unpacked rows in scope.
	// Already-packed rows are silently excluded whether selected or not.
	const targetRows = selected.length
		? selected.filter(r => !r.parcel_number)
		: (frm.doc.items || []).filter(r => !r.parcel_number)

	if (!targetRows.length) {
		frappe.msgprint(
			selected.length
				? __('Selected rows are already packed. Unpack them first or choose other rows.')
				: __('Nothing to cartonize — all rows are already packed.')
		)
		return false
	}

	// When a selection was made, scope the backend call to only the unpacked rows from
	// that selection.  No selection → null payload → backend cartonizes all unpacked rows.
	const rowNamesPayload = selected.length ? JSON.stringify(targetRows.map(r => r.name)) : null

	frappe.call({
		method: 'shipstation_integration.cartonization.apply_cartonization_to_packing_slip',
		args: {
			packing_slip_name: frm.doc.name,
			row_names_json: rowNamesPayload,
		},
		freeze: true,
		callback(r) {
			const sol = r.message || {}
			frm.reload_doc().then(() => {
				render_parcel_indicators(frm)
				if ((sol.messages || []).length) {
					frappe.msgprint(sol.messages.join('<br>'))
				}
			})
		},
	})
	return false
}

function split_selected_rows(frm) {
	const selected = frm.fields_dict.items.grid.get_selected_children()
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

		const new_row = frappe.model.add_child(frm.doc, row.doctype, 'items')
		copyable_fields.forEach(fieldname => {
			new_row[fieldname] = row[fieldname]
		})
		// dn_detail is no_copy but must be preserved for split — both rows reference the same DN item
		new_row.dn_detail = row.dn_detail
		new_row.qty = 1
	})

	frm.fields_dict.items.grid.refresh()
	render_parcel_indicators(frm)
	refresh_parcel_details_display(frm, 'items')
	deselect_all_rows(frm)
	frm.dirty()
	return false
}

frappe.ui.form.on('Packing Slip', {
	setup: function (frm) {
		frm._ss_cartonization_enabled = false

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

		frm.set_query('dispatch_address_name', function () {
			return {
				filters: {
					is_your_company_address: 1,
				},
			}
		})

		// Header-level carrier: only show transporter suppliers
		frm.set_query('carrier', function () {
			return { filters: { is_transporter: 1 } }
		})

		// Item-row carrier: same filter
		frm.set_query('carrier', 'items', function () {
			return { filters: { is_transporter: 1 } }
		})

		// Header parcel template: narrow to templates for the selected carrier, if any
		frm.set_query('default_parcel_template', function () {
			if (frm.doc.carrier) {
				return { filters: { carrier: frm.doc.carrier } }
			}
			return {}
		})

		// Item-row parcel template: narrow to templates for the selected carrier, if any
		frm.set_query('parcel_template', 'items', function (doc, cdt, cdn) {
			const row = locals[cdt][cdn]
			const carrier = row.carrier || frm.doc.carrier
			if (carrier) {
				return { filters: { carrier } }
			}
			return {}
		})
	},

	refresh: function (frm) {
		if (frm.doc.carrier && !frm.service_code_map) {
			load_carrier_services(frm, frm.doc.carrier)
		}

		frappe.call({
			method: 'shipstation_integration.cartonization.is_cartonization_enabled',
			callback(r) {
				frm._ss_cartonization_enabled = !!r.message
				setup_shipping_actions(frm)
				setup_sscc_button(frm)
				setup_parcel_buttons(frm)
				render_parcel_indicators(frm)
				refresh_parcel_details_display(frm, 'items')
				if (frm._ss_cartonization_enabled) {
					fetch_source_handling_units(frm)
				} else {
					frm._source_hu_map = {}
				}

				if (frm.doc.delivery_note) {
					if (!frm.doc.shipping_address_name || !frm.doc.dispatch_address_name) {
						fetch_delivery_note_defaults(frm)
					}
				}
			},
		})
	},

	delivery_note: function (frm) {
		if (!frm.doc.delivery_note) return
		frm.__delivery_note_loaded = false
		fetch_delivery_note_defaults(frm)
	},

	shipping_address_name: function (frm) {
		const addr = frm.doc.shipping_address_name
		if (addr) {
			frappe.call({
				method: 'frappe.contacts.doctype.address.address.get_address_display',
				args: { address_dict: addr },
				callback: function (r) {
					if (r.message) frm.set_value('shipping_address', r.message)
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
				args: { address_dict: addr },
				callback: function (r) {
					if (r.message) frm.set_value('dispatch_address', r.message)
				},
			})
		} else {
			frm.set_value('dispatch_address', '')
		}
	},

	carrier: function (frm) {
		if (frm.doc.carrier) {
			load_carrier_services(frm, frm.doc.carrier)
		} else {
			frm.fields_dict['carrier_service'].set_data([])
			frm.service_code_map = null
		}
	},

	default_parcel_template: function (frm) {
		const template_name = frm.doc.default_parcel_template
		if (!template_name) return

		frappe.db.get_doc('Shipment Parcel Template', template_name).then(template => {
			if (!template || !frm.doc.items?.length) return

			// Template stores length/width/height in cm and weight in kg (fixed by the field labels)
			const updates = frm.doc.items.map(row =>
				frappe.model.set_value(row.doctype, row.name, {
					parcel_template: template_name,
					carrier: template.carrier || row.carrier || '',
					parcel_length: template.length,
					parcel_width: template.width,
					parcel_height: template.height,
					dimension_uom: 'Centimeter',
					parcel_weight: template.weight || 0,
					parcel_weight_uom: 'Kg',
				})
			)

			Promise.all(updates).then(() => {
				if (template.carrier && !frm.doc.carrier) {
					frm.set_value('carrier', template.carrier)
				}
				frm.refresh_field('items')
				refresh_parcel_details_display(frm, 'items')
				frm.dirty()
			})
		})
	},

	items_add: function (frm) {
		render_parcel_indicators(frm)
	},

	items_remove: function (frm) {
		render_parcel_indicators(frm)
	},

	items_move: function (frm) {
		frappe.msgprint(__('Moving rows may require re-packing existing parcels.'), __('Warning'))
		render_parcel_indicators(frm)
	},
})

frappe.ui.form.on('Packing Slip Item', {
	parcel_number: function (frm) {
		render_parcel_indicators(frm)
	},

	parcel_template: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (!row.parcel_template) return

		frappe.db.get_doc('Shipment Parcel Template', row.parcel_template).then(template => {
			if (!template) return

			// Template stores length/width/height in cm and weight in kg (fixed by the field labels)
			frappe.model.set_value(cdt, cdn, {
				carrier: template.carrier || '',
				parcel_length: template.length,
				parcel_width: template.width,
				parcel_height: template.height,
				dimension_uom: 'Centimeter',
				parcel_weight: template.weight || 0,
				parcel_weight_uom: 'Kg',
			})
			frm.refresh_field('items')

			if (template.carrier && !frm.doc.carrier) {
				frm.set_value('carrier', template.carrier)
			}

			refresh_parcel_details_display(frm, 'items', cdn)
		})
	},

	carrier: function (frm, cdt, cdn) {
		const row = locals[cdt][cdn]
		if (row.carrier && !frm.doc.carrier) {
			frm.set_value('carrier', row.carrier)
		}
	},

	parcel_length: function (frm, cdt, cdn) {
		check_template_match(frm, cdt, cdn)
		refresh_parcel_details_display(frm, 'items', cdn)
	},

	parcel_width: function (frm, cdt, cdn) {
		check_template_match(frm, cdt, cdn)
		refresh_parcel_details_display(frm, 'items', cdn)
	},

	parcel_height: function (frm, cdt, cdn) {
		check_template_match(frm, cdt, cdn)
		refresh_parcel_details_display(frm, 'items', cdn)
	},

	dimension_uom: function (frm, cdt, cdn) {
		refresh_parcel_details_display(frm, 'items', cdn)
	},

	parcel_weight: function (frm, cdt, cdn) {
		refresh_parcel_details_display(frm, 'items', cdn)
	},

	parcel_weight_uom: function (frm, cdt, cdn) {
		refresh_parcel_details_display(frm, 'items', cdn)
	},
})

function load_carrier_services(frm, supplier_name) {
	if (!supplier_name) return

	// Resolve ERPNext supplier → ShipEngine carrier_id, then fetch live services.
	frappe.call({
		method: 'shipstation_integration.carriers.get_carrier_id_for_supplier',
		args: { supplier_name },
		callback: function (r) {
			if (!r.message) return
			const carrier_id = r.message

			frappe.call({
				method: 'shipstation_integration.carriers.list_carrier_services',
				args: { carrier_id },
				callback: function (r2) {
					const services = r2.message || []
					if (!services.length) return

					frm.service_code_map = {}
					const options = [''].concat(
						services.map(s => {
							const label = s.name || s.service_code
							frm.service_code_map[label] = s.service_code
							return label
						})
					)

					// set_data() is the canonical Frappe method on ControlAutocomplete —
					// it sets awesomplete.list AND caches in this._data so suggestions
					// persist and re-appear after the field value is cleared.
					frm.fields_dict['carrier_service'].set_data(options)
				},
			})
		},
	})
}

function fetch_weight_from_delivery_note(frm, dn) {
	let net_weight = 0
	;(dn.items || []).forEach(item => {
		net_weight += flt(item.total_weight) || flt(item.net_weight) * flt(item.qty) || 0
	})
	const weight_uom = dn.items?.[0]?.weight_uom || 'Pound'
	frm.set_value('net_weight_pkg', flt(net_weight, 2))
	frm.set_value('net_weight_uom', weight_uom)
	if (!frm.doc.gross_weight_pkg) {
		frm.set_value('gross_weight_pkg', flt(net_weight, 2))
		frm.set_value('gross_weight_uom', weight_uom)
	}
}

function has_tracking_number(frm) {
	return (frm.doc.items || []).some(row => row.tracking_number)
}

function get_carrier_from_items(frm) {
	const items = frm.doc.items || []
	for (const row of items) {
		if (row.carrier) return row.carrier
	}
	return null
}

function setup_shipping_actions(frm) {
	frappe.call({
		method: 'frappe.client.get_value',
		args: {
			doctype: 'Shipstation Settings',
			filters: { enabled: 1 },
			fieldname: ['name', 'enable_shipstation_api'],
		},
		callback: function (r) {
			if (!r.message || !r.message.enable_shipstation_api) return

			if (!has_tracking_number(frm)) {
				const has_carrier = frm.doc.carrier || get_carrier_from_items(frm)
				const has_service = frm.doc.carrier_service

				if (has_carrier && has_service) {
					frm.add_custom_button(__('Create Label'), () => create_label_direct(frm), __('Shipping'))
				} else if (has_carrier) {
					frm.add_custom_button(__('Create Label'), () => create_label_pick_service(frm), __('Shipping'))
				} else {
					frm.add_custom_button(__('Create Label'), () => create_shipping_label(frm), __('Shipping'))
				}

				frm.add_custom_button(__('Compare Rates'), () => get_shipping_rates(frm), __('Shipping'))
			}

			frm.add_custom_button(
				__('Compare Rates'),
				() => confirm_then_create_label(frm, () => get_shipping_rates(frm)),
				__('Shipping')
			)
		},
	})
}

function get_shipping_rates(frm) {
	if (!frm.doc.shipping_address_name) {
		frappe.msgprint(__('Please select a shipping address first.'))
		return
	}

	if (!frm.doc.dispatch_address_name) {
		frappe.msgprint(__('Please select a dispatch (ship from) address first.'))
		return
	}

	const has_parcel_dims = (frm.doc.items || []).some(r => r.parcel_number)
	if (!has_parcel_dims) {
		frappe.msgprint(__('Please assign items to parcels (set Parcel #) before comparing rates.'))
		return
	}

	frappe.call({
		method: 'shipstation_integration.rates.get_rates_for_packing_slip',
		args: { packing_slip: frm.doc.name },
		freeze: true,
		freeze_message: __('Fetching shipping rates...'),
		callback: function (r) {
			if (r.message && r.message.length) {
				show_rates_dialog(frm, r.message)
			} else {
				frappe.msgprint(__('No rates returned. Please check carrier configuration.'))
			}
		},
		error: function (err) {
			frappe.msgprint(__('Error fetching rates: {0}', [err.message || 'Unknown error']))
		},
	})
}

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

function create_label_with_rate(frm, carrier_id, service_code) {
	frappe.call({
		method: 'shipstation_integration.labels.create_label_for_packing_slip',
		args: {
			packing_slip: frm.doc.name,
			carrier_id,
			service_code,
		},
		freeze: true,
		freeze_message: __('Creating shipping label...'),
		callback: function (r) {
			if (r.message) show_label_success(frm, r.message)
		},
		error: function (err) {
			frappe.msgprint(__('Error creating label: {0}', [err.message || 'Unknown error']))
		},
	})
}

function create_label_direct(frm) {
	const carrier_supplier = frm.doc.carrier || get_carrier_from_items(frm)
	const service_display = frm.doc.carrier_service

	if (!carrier_supplier) {
		frappe.msgprint(__('Please select a carrier first.'))
		return
	}
	if (!service_display) {
		frappe.msgprint(__('Please select a carrier service first.'))
		return
	}

	let service_code = service_display
	if (frm.service_code_map && frm.service_code_map[service_display]) {
		service_code = frm.service_code_map[service_display]
	} else if (service_display.includes(' ') || /[A-Z]/.test(service_display.charAt(0))) {
		console.warn('Service code map not found, service_display may be friendly name:', service_display)
	}

	frappe.call({
		method: 'shipstation_integration.carriers.get_carrier_id_for_supplier',
		args: { supplier_name: carrier_supplier },
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

function show_label_success(frm, results) {
	const list = Array.isArray(results) ? results : [results]

	// Apply tracking data to the live form rows by parcel_number.
	// The backend already wrote these to DB via db.set_value; updating
	// in-memory avoids a reload_doc() that would wipe unsaved SSCC codes.
	list.forEach(result => {
		const parcel = result.parcel_number ? __('Parcel {0}', [result.parcel_number]) : __('Label')
		const tracking = result.tracking_number || ''
		const msg = tracking ? __(`{0} created — {1}`, [parcel, tracking]) : __(`{0} created`, [parcel])
		frappe.show_alert({ message: msg, indicator: 'green' }, 7)

		if (!result.parcel_number) return
		;(frm.doc.items || []).forEach(row => {
			if (row.parcel_number !== result.parcel_number) return
			frappe.model.set_value(row.doctype, row.name, {
				tracking_number: result.tracking_number || '',
				label_url: result.label_download || '',
			})
		})
	})

	frm.refresh_field('items')
}

function create_label_pick_service(frm) {
	const carrier_supplier = frm.doc.carrier || get_carrier_from_items(frm)

	frappe.call({
		method: 'shipstation_integration.carriers.resolve_carrier_for_label',
		args: { supplier_name: carrier_supplier },
		callback: function (r) {
			const result = r.message || {}
			if (!result.carrier_id) {
				if (result.status === 'not_synced') {
					frappe.msgprint(__('No carrier data found. Please sync carriers in Shipstation Settings first.'))
				} else {
					const available = (result.available || []).join(', ') || __('none')
					frappe.msgprint(
						__(
							'{0} is not connected to your ShipEngine account. Available carriers: {1}. Please add {0} in your ShipStation/ShipEngine carrier settings.',
							[carrier_supplier, available]
						)
					)
				}
				return
			}
			const carrier_id = result.carrier_id
			const dialog = new frappe.ui.Dialog({
				title: __('Select Service for {0}', [carrier_supplier]),
				fields: [
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
					const service_label = dialog.get_value('service_code')
					const service_code = dialog.service_code_map?.[service_label] || service_label
					dialog.hide()
					create_label_with_rate(frm, carrier_id, service_code)
				},
			})
			load_services_for_dialog(dialog, carrier_id)
			dialog.show()
		},
	})
}

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

function show_carrier_selection_dialog(frm, carriers) {
	const carrier_options = carriers.map(c => ({
		value: c.carrier_id,
		label: c.name || c.friendly_name || c.carrier_code || c.carrier_id,
	}))

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
				default: frm.doc.carrier,
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
					if (carrier) load_services_for_dialog(dialog, carrier.value)
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
			const carrier = carrier_options.find(c => c.label === carrier_label)
			if (!carrier) {
				frappe.msgprint(__('Please select a valid carrier.'))
				return
			}
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
		args: { delivery_note: frm.doc.delivery_note },
		callback(r) {
			const accounts = r.message || []
			if (!accounts.length) {
				dialog.set_df_property('shipping_account', 'hidden', 1)
				return
			}
			dialog.set_df_property('shipping_account', 'hidden', 0)
			const options = accounts.map(a => a.shipping_account_number)
			dialog.set_df_property('shipping_account', 'options', options)
			dialog.shipping_account_map = {}
			accounts.forEach(a => {
				dialog.shipping_account_map[a.shipping_account_number] = a
			})
		},
	})
}

function load_services_for_dialog(dialog, carrier_id) {
	frappe.call({
		method: 'shipstation_integration.carriers.list_carrier_services',
		args: { carrier_id },
		callback: function (r) {
			if (r.message && r.message.length > 0) {
				const service_options = r.message.map(s => s.name || s.service_code)
				dialog.service_code_map = {}
				r.message.forEach(s => {
					const label = s.name || s.service_code
					dialog.service_code_map[label] = s.service_code
				})
				const service_field = dialog.fields_dict.service_code
				if (service_field && service_field.set_data) {
					service_field.set_data(service_options)
				} else if (service_field && service_field.awesomplete) {
					service_field.awesomplete.list = service_options
				}
			} else {
				const service_field = dialog.fields_dict.service_code
				if (service_field && service_field.set_data) service_field.set_data([])
			}
		},
		error: function (err) {
			console.error('Error loading carrier services:', err)
		},
	})
}

function setup_sscc_button(frm) {
	if (!(frm.doc.items || []).some(r => r.parcel_number)) return

	frappe.call({
		method: 'frappe.client.get_value',
		args: {
			doctype: 'Shipstation Settings',
			filters: { enabled: 1 },
			fieldname: 'gs1_company_prefix',
		},
		callback: function (r) {
			if (!r.message || !r.message.gs1_company_prefix) return
			frm.add_custom_button(__('Generate SSCC'), () => generate_sscc(frm), __('Shipping'))
		},
	})
}

function generate_sscc(frm) {
	frappe.call({
		method: 'shipstation_integration.sscc.generate_packing_slip_sscc',
		args: { packing_slip: frm.doc.name },
		freeze: true,
		freeze_message: __('Generating SSCC codes...'),
		callback: function (r) {
			if (!r.message) return
			const { generated, skipped, codes } = r.message

			// Apply codes directly to the live form so unsaved field changes
			// are not lost.  The server does not save; the user saves normally.
			if (codes && codes.length) {
				codes.forEach(({ name, ucc128 }) => {
					frappe.model.set_value('Packing Slip Item', name, 'ucc128', ucc128)
				})
				frm.refresh_field('items')
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

function check_template_match(frm, cdt, cdn) {
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
				frm.refresh_field('items')
			}
		},
	})
}
