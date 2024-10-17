# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt


app_name = "shipstation_integration"
app_title = "Shipstation Integration"
app_publisher = "AgriTheory"
app_description = "Shipstation integration for ERPNext"
app_icon = "octicon octicon-file-directory"
app_color = "grey"
app_email = "support@agritheory.dev"
app_license = "MIT"

# Setup Wizard
# ------------
# setup_wizard_stages = "shipstation_integration.setup.get_setup_stages"

# Includes in <head>
# ------------------
extend_bootinfo = "shipstation_integration.shipstation_integration.boot.boot_session"

# include js, css files in header of desk.html
# app_include_css = "/assets/shipstation_integration/css/shipstation_integration.css"
app_include_js = ["shipstation_integration.bundle.js"]

# include js, css files in header of web template
# web_include_css = "/assets/shipstation_integration/css/shipstation_integration.css"
# web_include_js = "/assets/shipstation_integration/js/shipstation_integration.js"

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views

doctype_js = {
	"Delivery Note": "public/js/delivery_note.js",
	"Sales Order": "public/js/sales_order.js",
}

doctype_list_js = {
	"Sales Order": "public/js/sales_order_list.js",
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

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

scheduler_events = {
	"all": [
		"shipstation_integration.orders.queue_orders",
		"shipstation_integration.shipments.queue_shipments",
		"shipstation_integration.tags.queue_tags",
	]
}

# Testing
# -------

# before_tests = "shipstation_integration.install.before_tests"

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

override_doctype_class = {
	"Sales Order": "shipstation_integration.shipstation_integration.overrides.sales_order.ShipStationSalesOrder",
}
