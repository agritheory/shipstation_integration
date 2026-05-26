# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from shipstation_integration.ltl import ShipstationLTL

# Fields written or chosen during the post-submit LTL quote → book → pickup flow.
LTL_ALLOW_ON_SUBMIT_FIELDS = [
	"preferred_carrier",
	"carrier_id",
	"carrier_service_level",
	"package_type",
	"package_type_code",
	"quote_or_offer_id",
	"quote_or_offer_transaction_id",
	"pickup_id",
	"accepted_quotation",
	"estimated_delivery_date",
	"request_spot_quote",
	"billing_type",
	"payment_terms",
	*ShipstationLTL().accessorial_services_map.keys(),
]

# Standard Shipment fields populated by LTL APIs after submit.
STANDARD_ALLOW_ON_SUBMIT_FIELDS = [
	"awb_number",
	"carrier",
	"carrier_service",
	"shipment_id",
	"tracking_url",
	"status",
]


def execute():
	for fieldname in LTL_ALLOW_ON_SUBMIT_FIELDS:
		cf_name = frappe.db.get_value("Custom Field", {"dt": "Shipment", "fieldname": fieldname})
		if cf_name:
			frappe.db.set_value("Custom Field", cf_name, "allow_on_submit", 1)

	for fieldname in STANDARD_ALLOW_ON_SUBMIT_FIELDS:
		if frappe.db.get_value(
			"Property Setter",
			{"doc_type": "Shipment", "field_name": fieldname, "property": "allow_on_submit"},
		):
			continue
		make_property_setter(
			"Shipment",
			fieldname,
			"allow_on_submit",
			"1",
			"Check",
			for_doctype=False,
		)

	frappe.clear_cache(doctype="Shipment")
