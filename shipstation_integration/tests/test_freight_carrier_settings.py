# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe

from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.utils import get_shipment_company_for_ltl


def test_get_freight_carrier_settings_none_when_missing():
	"""get_freight_carrier_settings returns None for nonexistent company/supplier."""
	assert get_freight_carrier_settings("No Such Company", "No Such Supplier") is None


def test_get_freight_carrier_settings_resolves_doc():
	"""get_freight_carrier_settings resolves an existing FCS document and retrieves password field."""
	company = "Ambrosia Pie Company"
	supplier_name = frappe.get_value(
		"Supplier", {"supplier_name": "Test LTL Carrier", "is_transporter": 1}, "name"
	)

	existing = frappe.db.get_value(
		"Freight Carrier Settings", {"company": company, "supplier": supplier_name}, "name"
	)
	if existing:
		frappe.delete_doc("Freight Carrier Settings", existing, force=1, ignore_permissions=True)

	doc = frappe.new_doc("Freight Carrier Settings")
	doc.company = company
	doc.supplier = supplier_name
	doc.insert(ignore_permissions=True)
	doc.set("ltl_api_key", "test-key-for-unit-test")
	doc.save(ignore_permissions=True)

	try:
		found = get_freight_carrier_settings(company, supplier_name)
		assert found is not None
		assert found.name == doc.name
		assert found.get_password("ltl_api_key") == "test-key-for-unit-test"
	finally:
		frappe.delete_doc("Freight Carrier Settings", doc.name, force=1, ignore_permissions=True)


def test_get_shipment_company_for_ltl_from_shipment():
	"""get_shipment_company_for_ltl extracts company from shipment based on pickup configuration."""
	names = frappe.get_all(
		"Shipment",
		filters={"freight_type": "LTL", "docstatus": 0},
		pluck="name",
		order_by="creation desc",
		limit_page_length=1,
	)
	shipment = frappe.get_doc("Shipment", names[0])

	shipment.pickup_from_type = "Company"
	shipment.pickup_company = "Ambrosia Pie Company"
	assert get_shipment_company_for_ltl(shipment) == "Ambrosia Pie Company"
