# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from . import __version__ as app_version

app_name = "shipstation_integration"
app_title = "Shipstation Integration"
app_publisher = "AgriTheory"
app_description = "Shipstation integration for ERPNext"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "support@agritheory.dev"
app_license = "MIT"

required_apps = ["frappe/erpnext", "agritheory/beam", "agritheory/inventory_tools"]

# Setup Wizard
# ------------
# setup_wizard_stages = "shipstation_integration.setup.get_setup_stages"

# Includes in <head>
# ------------------
extend_bootinfo = "shipstation_integration.shipstation_integration.boot.boot_session"

# include js, css files in header of desk.html
app_include_css = "/assets/shipstation_integration/css/shipstation_integration.css"
app_include_js = ["shipstation_integration.bundle.js"]

jinja = {
	"methods": [
		"shipstation_integration.parcel_uom_conversion.format_parcel_details",
	]
}

# include js, css files in header of web template
# web_include_css = "/assets/shipstation_integration/css/shipstation_integration.css"
# web_include_js = "/assets/shipstation_integration/js/shipstation_integration.js"

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views

doctype_js = {
	"Delivery Note": "public/js/delivery_note.js",
	"Packing Slip": ["public/js/parcel_details.js", "public/js/packing_slip.js"],
	"Shipment": [
		"public/js/parcel_details.js",
		"public/js/shipment_custom.js",
		"public/js/shipment_pack.js",
	],
	"Sales Order": "public/js/sales_order.js",
	"Supplier": "public/js/supplier.js",
	"Customer": "public/js/customer.js",
	"Shipment Parcel Template": "public/js/shipment_parcel_template.js",
}

doctype_list_js = {
	"Sales Order": "public/js/sales_order_list.js",
	"Delivery Note": "public/js/delivery_note_list.js",
}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Website user home page (by function)
# get_website_user_home_page = "shipstation_integration.utils.get_home_page"

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Installation
# ------------
after_migrate = ["shipstation_integration.install.add_custom_queue"]
after_install = "shipstation_integration.install.after_install"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "shipstation_integration.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Packing Slip": {
		"before_submit": "shipstation_integration.packing_slip.before_submit",
		"on_submit": "shipstation_integration.packing_slip.on_submit",
	},
	"Shipment": {
		"before_validate": (
			"shipstation_integration.shipstation_integration.overrides.delivery_note.before_validate_shipment"
		),
		"before_submit": "shipstation_integration.shipment_pack.before_submit",
		"on_submit": "shipstation_integration.shipment_pack.on_submit",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": [
		"shipstation_integration.tags.queue_tags",
		"shipstation_integration.orders.queue_orders",
		"shipstation_integration.shipments.queue_shipments",
	]
}

# Testing
# -------

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "shipstation_integration.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "shipstation_integration.task.get_dashboard_data"
# }

override_whitelisted_methods = {
	"erpnext.stock.doctype.delivery_note.delivery_note.make_packing_slip": "shipstation_integration.cartonization.make_packing_slip_with_optional_cartonization",
	"erpnext.stock.doctype.delivery_note.delivery_note.make_shipment": "shipstation_integration.cartonization.make_shipment_with_optional_cartonization",
}

override_doctype_class = {
	"Sales Order": "shipstation_integration.shipstation_integration.overrides.sales_order.ShipStationSalesOrder",
	"Shipment": "shipstation_integration.shipstation_integration.overrides.shipment.ShipStationShipment",
	"Shipment Parcel Template": "shipstation_integration.shipstation_integration.overrides.shipment_parcel_template.ShipstationShipmentParcelTemplate",
	"Packing Slip": "shipstation_integration.shipstation_integration.overrides.packing_slip.ShipstationPackingSlip",
}

# Maps a substring of Freight Carrier Settings.base_url to the dotted import path of
# the BaseLTL subclass that handles that provider.  Entries are checked in definition
# order; first match wins.  No imports belong here — plain strings only.
ltl_providers = {
	"shipengine.com": "shipstation_integration.ltl.ShipstationLTL",
	"shipstation.com": "shipstation_integration.ltl.ShipstationLTL",
	# WWEX: staging uses speedship.staging-wwex.com (matches wwex.com),
	# production uses www.speedship.com (matches speedship.com)
	"wwex.com": "shipstation_integration.shipstation_integration.freight_providers.wwex_ltl.WwexLTL",
	"speedship.com": "shipstation_integration.shipstation_integration.freight_providers.wwex_ltl.WwexLTL",
	"banyantechnology.com": "shipstation_integration.shipstation_integration.freight_providers.banyan_ltl.BanyanLTL",
	"traffictech.com": "shipstation_integration.shipstation_integration.freight_providers.traffictech_ltl.TrafficTechLTL",
	"odfl.com": "shipstation_integration.shipstation_integration.freight_providers.odfl_ltl.OdflLTL",
}
