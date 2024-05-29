frappe.listview_settings["Tag"] = {
    refresh: function (listview) {
        listview.page.add_inner_button("Sync Tags from ShipStation", function () {
            frappe.call({
                method: 'shipstation_integration.tags.list_tags',
                callback: function (r) {
                    listview.refresh();
                }
            });
        });
    },
};