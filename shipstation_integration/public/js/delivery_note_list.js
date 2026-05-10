// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.listview_settings['Delivery Note'] = {
	onload: function (listview) {
		if (!frappe.boot.inventory_tools_installed) {
			return
		}

		listview.page.add_action_item(__('Cartonize'), () => cartonize_delivery_note_from_list(listview))
	},
}

function cartonize_delivery_note_from_list(listview) {
	const checked = listview.get_checked_items()
	const names = Array.isArray(checked) && checked.length ? checked.map(d => (typeof d === 'string' ? d : d.name)) : []

	if (names.length !== 1) {
		frappe.msgprint(__('Select exactly one Delivery Note to cartonize.'))
		return
	}

	const dn_name = names[0]

	frappe.call({
		method: 'shipstation_integration.cartonization.preview_cartonization_for_delivery_note',
		args: { delivery_note_name: dn_name },
		freeze: true,
		freeze_message: __('Running cartonization...'),
		callback: function (r) {
			const sol = r.message || {}
			const bins = sol.bins || []
			const rows = bins
				.map(
					b =>
						`<tr><td>${frappe.utils.escape_html(String(b.parcel_template || '-'))}</td>` +
						`<td>${b.bin_number}</td>` +
						`<td>${(b.items || [])
							.map(x => frappe.utils.escape_html(`${x.item_code} (${x.qty})`))
							.join(', ')}</td></tr>`
				)
				.join('')

			const warn = sol.skipped?.length
				? `<p class="text-warning">${__('Some lines were skipped (see messages).')}</p>`
				: ''

			const table =
				`${warn}` +
				`<table class="table table-bordered"><thead><tr>` +
				`<th>${__('Parcel template')}</th><th>#</th><th>${__('Lines')}</th></tr></thead>` +
				`<tbody>${rows}</tbody></table>`

			const dlg = new frappe.ui.Dialog({
				title: __('Cartonization Preview'),
				fields: [{ fieldname: 'table_html', fieldtype: 'HTML', options: table }],
				primary_action_label: __('Close'),
				primary_action() {
					dlg.hide()
				},
			})

			if (sol.messages && sol.messages.length) {
				frappe.msgprint(sol.messages.join('<br>'))
			}

			dlg.show()

			frappe.db.get_value('Delivery Note', dn_name, 'docstatus').then(res => {
				const docstatus = res.message
				if (docstatus === 0) {
					dlg.set_secondary_action_label(__('Create Packing Slip'))
					dlg.set_secondary_action(() => {
						dlg.hide()
						frappe.call({
							method: 'shipstation_integration.cartonization.insert_cartonized_packing_slip_from_delivery_note',
							args: { delivery_note_name: dn_name },
							freeze: true,
							callback(createRes) {
								if (createRes.message) {
									frappe.set_route('Form', 'Packing Slip', createRes.message)
								}
							},
						})
					})
				}
			})
		},
	})
}
