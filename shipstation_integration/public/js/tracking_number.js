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

	const latest_event_time = all_events[all_events.length - 1].event_time

	draw_events(map, all_events, latest_event_time)

	wrapper.find('#tn-stage-filter').on('change', function () {
		const stage = this.value
		const filtered = stage ? all_events.filter((e) => e.stage === stage) : all_events
		draw_events(map, filtered, latest_event_time)
	})
}

function red_pin_icon() {
	return L.divIcon({
		className: '',
		html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 25 41" width="25" height="41">
			<path d="M12.5 0C5.6 0 0 5.6 0 12.5C0 21.9 12.5 41 12.5 41S25 21.9 25 12.5C25 5.6 19.4 0 12.5 0z" fill="#e74c3c" stroke="#c0392b" stroke-width="1"/>
			<circle cx="12.5" cy="12.5" r="5" fill="white"/>
		</svg>`,
		iconSize: [25, 41],
		iconAnchor: [12, 41],
		popupAnchor: [1, -34],
	})
}

function draw_events(map, events, latest_event_time) {
	const to_remove = []
	map.eachLayer((layer) => {
		if (!(layer instanceof L.TileLayer)) to_remove.push(layer)
	})
	to_remove.forEach((layer) => map.removeLayer(layer))

	if (!events.length) return

	const coords = events.map((e) => [e.latitude, e.longitude])

	const esc = frappe.utils.escape_html
	events.forEach((e, i) => {
		const is_latest = e.event_time === latest_event_time
		const title = e.stage || `Event ${i + 1}`
		const location_str =
			e.location || [e.city, e.state, e.country].filter(Boolean).join(', ')
		const popup = `
			<b>${esc(title)}</b><br>
			${esc(e.description || '')}<br>
			<small>${esc(location_str)}</small><br>
			<small>${esc(e.event_time || '')}</small>
		`
		const marker = is_latest
			? L.marker([e.latitude, e.longitude], { icon: red_pin_icon() })
			: L.marker([e.latitude, e.longitude])
		marker.bindPopup(popup).addTo(map)
	})

	map.fitBounds(L.latLngBounds(coords), { padding: [30, 30] })
}
