frappe.listview_settings['Carrier'] = {
	onload: function (listview) {
		listview.page.add_inner_button(__('Update Carriers'), function () {
			frappe.call({
				method: 'shipstation_integration.shipstation_integration.doctype.carrier.carrier.fetch_carriers',
				freeze: true,
				freeze_message: __('Updating ...'),
				callback: function (r) {
					frappe.msgprint(r.message)
					listview.refresh()
				},
			})
		})
	},
}
