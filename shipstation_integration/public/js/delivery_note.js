frappe.ui.form.on('Delivery Note', {
	refresh: frm => {
		shipping.shipstation(frm)

		if (frm.doc.docstatus === 1 && frm.doc.shipstation_order_id) {
			frm
				.add_custom_button(__('Fetch Shipment'), () => {
					frappe.call({
						method: 'shipstation_integration.shipping.fetch_shipment',
						args: {
							delivery_note: frm.doc.name,
						},
						freeze: true,
						callback: function (r) {
							if (r.message) {
								frappe.msgprint(`A shipment was fetched from Shipstation and created at ${r.message}`)
							} else {
								frappe.msgprint(
									`No new shipment(s) were found against Shipstation ID: ${frm.doc.shipstation_order_id.bold()}`
								)
							}
						},
					})
				})
				.removeClass('btn-default')
				.addClass('btn-primary')
		}

		// Add rate shopping button for submitted Delivery Notes with shipping address
		if (frm.doc.docstatus === 1 && frm.doc.shipping_address_name && !frm.doc.tracking_number) {
			add_rate_shopping_button(frm)
		}
	},
})

function add_rate_shopping_button(frm) {
	frm.add_custom_button(
		__('Get Shipping Rates'),
		() => {
			frappe.call({
				method: 'shipstation_integration.rates.get_rates_for_delivery_note',
				args: {
					delivery_note: frm.doc.name,
				},
				freeze: true,
				freeze_message: __('Fetching shipping rates...'),
				callback: function (r) {
					if (r.message && r.message.length > 0) {
						show_rate_selection_dialog(frm, r.message)
					} else {
						frappe.msgprint(__('No shipping rates found for this delivery.'))
					}
				},
				error: function (r) {
					frappe.msgprint(__('Failed to fetch shipping rates. Please check your ShipStation API configuration.'))
				},
			})
		},
		__('ShipStation')
	)
}

function show_rate_selection_dialog(frm, rates) {
	// Format rates for display
	const rate_options = rates.map(rate => {
		const rawAmount = rate.shipping_amount?.amount ?? rate.shipping_amount ?? 0
		const amount = typeof rawAmount === 'number' ? rawAmount : parseFloat(rawAmount) || 0
		const currency = rate.shipping_amount?.currency || 'USD'
		const delivery_info = rate.delivery_days ? ` (${rate.delivery_days} days)` : ''
		return {
			label: `${rate.carrier_name} - ${rate.service_type || rate.service_code} - ${currency} ${amount.toFixed(2)}${delivery_info}`,
			value: rate.rate_id,
			rate: rate,
		}
	})

	const dialog = new frappe.ui.Dialog({
		title: __('Select Shipping Rate'),
		fields: [
			{
				fieldname: 'rates_html',
				fieldtype: 'HTML',
				options: `<p class="text-muted">${__('Select a shipping rate to purchase a label:')}</p>`,
			},
			{
				fieldname: 'selected_rate',
				fieldtype: 'Select',
				label: __('Shipping Rate'),
				options: rate_options.map(r => r.label).join('\n'),
				reqd: 1,
			},
			{
				fieldname: 'rate_details',
				fieldtype: 'HTML',
				options: '',
			},
		],
		primary_action_label: __('Create Label'),
		primary_action: function () {
			const selected_label = dialog.get_value('selected_rate')
			const selected = rate_options.find(r => r.label === selected_label)

			if (selected) {
				dialog.hide()
				create_label_from_rate(frm, selected.rate)
			}
		},
	})

	// Update rate details when selection changes
	dialog.fields_dict.selected_rate.$input.on('change', function () {
		const selected_label = dialog.get_value('selected_rate')
		const selected = rate_options.find(r => r.label === selected_label)

		if (selected && selected.rate) {
			const rate = selected.rate
			const rawAmount = rate.shipping_amount?.amount ?? rate.shipping_amount ?? 0
			const amount = typeof rawAmount === 'number' ? rawAmount : parseFloat(rawAmount) || 0
			const currency = rate.shipping_amount?.currency || 'USD'

			let details_html = `
				<div class="rate-details" style="padding: 15px; background: var(--bg-light-gray); border-radius: 8px; margin-top: 10px;">
					<h5>${rate.carrier_name}</h5>
					<p><strong>${__('Service')}:</strong> ${rate.service_type || rate.service_code}</p>
					<p><strong>${__('Cost')}:</strong> ${currency} ${amount.toFixed(2)}</p>
			`

			if (rate.delivery_days) {
				details_html += `<p><strong>${__('Estimated Delivery')}:</strong> ${rate.delivery_days} days</p>`
			}

			if (rate.estimated_delivery_date) {
				details_html += `<p><strong>${__('Delivery Date')}:</strong> ${rate.estimated_delivery_date}</p>`
			}

			details_html += '</div>'

			dialog.fields_dict.rate_details.$wrapper.html(details_html)
		}
	})

	dialog.show()
}

function create_label_from_rate(frm, rate) {
	frappe.call({
		method: 'shipstation_integration.labels.create_label_for_delivery_note',
		args: {
			delivery_note: frm.doc.name,
			rate_id: rate.rate_id,
		},
		freeze: true,
		freeze_message: __('Creating shipping label...'),
		callback: function (r) {
			if (r.message) {
				const tracking = r.message.tracking_number || ''
				frappe.show_alert({
					message: __('Label created! Tracking: {0}', [tracking]),
					indicator: 'green',
				})
				frm.reload_doc()
			}
		},
		error: function (r) {
			frappe.msgprint(__('Failed to create shipping label.'))
		},
	})
}
