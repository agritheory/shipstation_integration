frappe.listview_settings["Shipstation Settings"] = {
	onload: function (listview) {
		listview.page.add_inner_button("Sync Tags from ShipStation", function () {
			frappe.call({
				method: "shipstation_integration.tags.list_tags",
				callback: function (r) {
					listview.refresh();
				},
			});
		});
	},
};