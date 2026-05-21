// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.listview_settings['Tracking Number'] = {
	get_coords_method:
		'shipstation_integration.shipstation_integration.doctype.tracking_number.tracking_number.get_tracking_number_map_data',

	onload(listview) {
		if (listview._tn_map_patched) return
		listview._tn_map_patched = true

		const _orig = frappe.views.MapView.prototype.render_map_data
		frappe.views.MapView.prototype.render_map_data = function () {
			if (this.doctype !== 'Tracking Number') return _orig.call(this)
			render_tn_list_map(this, this.coords)
		}
	},
}

const STATUS_COLORS = {
	Delivered: '#27ae60',
	OutForDelivery: '#2980b9',
	InTransit: '#3498db',
	AvailableForPickup: '#1abc9c',
	InfoReceived: '#f39c12',
	Exception: '#e74c3c',
	DeliveryFailure: '#c0392b',
	Expired: '#95a5a6',
	NotFound: '#bdc3c7',
}
const DEFAULT_COLOR = '#7f8c8d'

function colored_pin_icon(color) {
	return L.divIcon({
		className: '',
		html: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 25 41" width="25" height="41">
			<path d="M12.5 0C5.6 0 0 5.6 0 12.5C0 21.9 12.5 41 12.5 41S25 21.9 25 12.5C25 5.6 19.4 0 12.5 0z" fill="${color}" stroke="#00000033" stroke-width="1"/>
			<circle cx="12.5" cy="12.5" r="5" fill="white"/>
		</svg>`,
		iconSize: [25, 41],
		iconAnchor: [12, 41],
		popupAnchor: [1, -34],
	})
}

function add_legend(map, statuses) {
	if (map._tn_legend) {
		map._tn_legend.remove()
	}
	const legend = L.control({ position: 'bottomright' })
	legend.onAdd = function () {
		const div = L.DomUtil.create('div')
		div.style.cssText =
			'background:white;padding:10px 14px;border-radius:6px;border:1px solid #ccc;font-size:13px;line-height:1.8;'
		div.innerHTML =
			'<strong style="display:block;margin-bottom:4px;">' +
			__('Status') +
			'</strong>' +
			statuses
				.map(s => {
					const color = STATUS_COLORS[s] || DEFAULT_COLOR
					return (
						`<span style="display:inline-block;width:12px;height:12px;border-radius:50%;` +
						`background:${color};margin-right:6px;vertical-align:middle;"></span>` +
						frappe.utils.escape_html(s)
					)
				})
				.join('<br>')
		return div
	}
	legend.addTo(map)
	map._tn_legend = legend
}

function render_tn_list_map(view, map_data) {
	const map = view.map
	if (!map) return

	const to_remove = []
	map.eachLayer(layer => {
		if (!(layer instanceof L.TileLayer)) to_remove.push(layer)
	})
	to_remove.forEach(layer => map.removeLayer(layer))

	const features = (map_data && map_data.features) || []
	if (!features.length) return

	const esc = frappe.utils.escape_html
	const coords = []

	features.forEach(feature => {
		const [lon, lat] = feature.geometry.coordinates
		const props = feature.properties
		const color = STATUS_COLORS[props.status] || DEFAULT_COLOR
		const form_link = frappe.utils.get_form_link('Tracking Number', props.name)
		const popup = `
			<b><a href="${form_link}">${esc(props.tracking_number || props.name)}</a></b><br>
			${esc(props.status || '')}<br>
			<small>${esc(props.location || '')}</small>
		`
		L.marker([lat, lon], { icon: colored_pin_icon(color) })
			.bindPopup(popup)
			.addTo(map)
		coords.push([lat, lon])
	})

	if (coords.length) {
		map.fitBounds(L.latLngBounds(coords), { padding: [30, 30] })
	}

	const present_statuses = [...new Set(features.map(f => f.properties.status).filter(Boolean))]
	if (present_statuses.length) {
		add_legend(map, present_statuses)
	}
}
