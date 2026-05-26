// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

// Parcel Details is an HTML field (no DB column). Grid display is computed at
// render time via the formatter below from parcel dimension/weight fields.

const PARCEL_DIM_UOM_ABBR = {
	Inch: '"',
	Centimeter: 'cm',
	Foot: "'",
	Millimeter: 'mm',
	Meter: 'm',
}

const PARCEL_WEIGHT_UOM_ABBR = {
	Pound: 'lbs',
	Kg: 'kg',
	Kilogram: 'kg',
	Ounce: 'oz',
	Gram: 'g',
}

function user_pref_parcel_abbrs() {
	const p = frappe.boot.parcel_uom || {}
	const dim_pref = p.dimension_uom || 'Centimeter'
	const wt_pref = p.weight_uom || 'Kg'
	return {
		dimAbbr: PARCEL_DIM_UOM_ABBR[dim_pref] || dim_pref || '',
		wtAbbr: PARCEL_WEIGHT_UOM_ABBR[wt_pref] || wt_pref || '',
	}
}

function parcel_detail_factors_for_row(row) {
	const p = frappe.boot.parcel_uom || {}
	const dim_map = p.to_dimension_pref || {}
	const wt_map = p.to_weight_pref || {}
	const abbr = user_pref_parcel_abbrs()
	const dk = row.dimension_uom || 'Centimeter'
	const wk = row.parcel_weight_uom || 'Kg'

	const has_dims = !!(row.parcel_length || row.parcel_width || row.parcel_height)
	const has_wt = !!row.parcel_weight

	let dim_factor = has_dims ? dim_map[dk] : 1
	if (dim_factor === undefined || dim_factor === null) dim_factor = has_dims ? 1 : 1

	let wt_factor = has_wt ? wt_map[wk] : 1
	if (wt_factor === undefined || wt_factor === null) wt_factor = has_wt ? 1 : 1

	return {
		dimension_factor: dim_factor,
		weight_factor: wt_factor,
		dim_abbr: abbr.dimAbbr,
		wt_abbr: abbr.wtAbbr,
	}
}

function row_has_parcel_context(row) {
	return !!row?.parcel_template
}

function format_parcel_details_row(row) {
	if (!row || !row_has_parcel_context(row)) return ''

	const l = row.parcel_length
	const w = row.parcel_width
	const h = row.parcel_height
	const wt = row.parcel_weight

	if (!l && !w && !h && !wt) return ''

	const opts = parcel_detail_factors_for_row(row)
	const dim_f = opts.dimension_factor != null ? opts.dimension_factor : 1
	const wt_f = opts.weight_factor != null ? opts.weight_factor : 1
	const dim_abbr = opts.dim_abbr || ''
	const wt_abbr = opts.wt_abbr || ''

	const parts = []
	if (l || w || h) {
		const fmt = v => (v % 1 === 0 ? v : flt(v, 2))
		const lv = flt((l || 0) * dim_f)
		const wv = flt((w || 0) * dim_f)
		const hv = flt((h || 0) * dim_f)
		parts.push(`${fmt(lv)}x${fmt(wv)}x${fmt(hv)}${dim_abbr}`)
	}
	if (wt) {
		parts.push(`${flt(flt(wt) * wt_f, 2)}${wt_abbr}`)
	}

	return parts.join(' ')
}

function parcel_details_formatter(value, df, options, doc) {
	return format_parcel_details_row(doc) || ''
}

function setup_parcel_details_formatters() {
	const formatter = parcel_details_formatter
	for (const doctype of ['Packing Slip Item', 'Shipment Delivery Note']) {
		const field = frappe.meta.docfield_map?.[doctype]?.parcel_details
		if (field) field.formatter = formatter
	}
}

function attach_parcel_details_formatter_to_grid(grid) {
	if (!grid) return
	const formatter = parcel_details_formatter
	const meta_field = frappe.meta.docfield_map?.[grid.doctype]?.parcel_details
	if (meta_field) meta_field.formatter = formatter
	if (grid.fields_map?.parcel_details) {
		grid.fields_map.parcel_details.formatter = formatter
	}
	for (const row of grid.grid_rows || []) {
		if (row.columns?.parcel_details?.df) {
			row.columns.parcel_details.df.formatter = formatter
		}
	}
}

function refresh_parcel_details_display(frm, grid_fieldname, cdn) {
	setup_parcel_details_formatters()
	const grid = frm.fields_dict[grid_fieldname]?.grid
	if (!grid) return

	attach_parcel_details_formatter_to_grid(grid)

	if (cdn) {
		const grid_row = grid.grid_rows.find(row => row.doc?.name === cdn)
		if (grid_row?.columns?.parcel_details?.df) {
			grid_row.columns.parcel_details.df.formatter = parcel_details_formatter
		}
		grid_row?.refresh_field('parcel_details')
		return
	}

	grid.grid_rows.forEach(row => row.refresh_field('parcel_details'))
}
