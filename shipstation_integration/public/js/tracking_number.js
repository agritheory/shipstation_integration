// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Tracking Number', {
	refresh(frm) {
		render_tracking_map(frm)
	},
})

function get_events_with_coords(frm) {
	return (frm.doc.tracking_number_event || [])
		.filter((e) => e.coordinates_source === 'API' || e.coordinates_source === 'Geocoded')
		.sort((a, b) => new Date(a.event_time) - new Date(b.event_time))
}

function render_tracking_map(frm) {
	const wrapper = frm.fields_dict.tracking_map.$wrapper
	wrapper.empty()

	const all_events = get_events_with_coords(frm)
	if (!all_events.length) {
		wrapper.html(
			`<p class="text-muted" style="padding: 8px;">${__('No location data available yet.')}</p>`
		)
		return
	}

	const stages = [...new Set(all_events.map((e) => e.stage).filter(Boolean))]
	const filter_options = stages
		.map((s) => `<option value="${frappe.utils.escape_html(s)}">${frappe.utils.escape_html(s)}</option>`)
		.join('')

	wrapper.html(`
		<div style="margin-bottom: 8px; display: flex; align-items: center; gap: 8px;">
			<label style="font-weight: 500; margin: 0;">${__('Filter by Stage')}</label>
			<select id="tn-stage-filter" class="form-control" style="width: auto;">
				<option value="">${__('All Stages')}</option>
				${filter_options}
			</select>
		</div>
		<div id="tn-tracking-map" style="height: 420px; border-radius: 6px; border: 1px solid var(--border-color);"></div>
	`)

	if (frm._tn_map) {
		frm._tn_map.remove()
		frm._tn_map = null
	}

	L.Icon.Default.imagePath = frappe.utils.map_defaults.image_path
	const first = all_events[0]
	const map = L.map('tn-tracking-map').setView([first.latitude, first.longitude], 6)
	L.tileLayer(frappe.utils.map_defaults.tiles, frappe.utils.map_defaults.options).addTo(map)
	frm._tn_map = map

	draw_events(map, all_events)

	wrapper.find('#tn-stage-filter').on('change', function () {
		const stage = this.value
		const filtered = stage ? all_events.filter((e) => e.stage === stage) : all_events
		draw_events(map, filtered)
	})
}

function draw_events(map, events) {
	map.eachLayer((layer) => {
		if (!(layer instanceof L.TileLayer)) map.removeLayer(layer)
	})

	if (!events.length) return

	const coords = events.map((e) => [e.latitude, e.longitude])

	L.polyline(coords, { color: '#4C72B0', weight: 2, opacity: 0.7 }).addTo(map)

	const esc = frappe.utils.escape_html
	events.forEach((e, i) => {
		const title = e.stage || `Event ${i + 1}`
		const location_str =
			e.location || [e.city, e.state, e.country].filter(Boolean).join(', ')
		const popup = `
			<b>${esc(title)}</b><br>
			${esc(e.description || '')}<br>
			<small>${esc(location_str)}</small><br>
			<small>${esc(e.event_time || '')}</small>
		`
		L.marker([e.latitude, e.longitude]).bindPopup(popup).addTo(map)
	})

	map.fitBounds(L.latLngBounds(coords), { padding: [30, 30] })
}
