# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.tests.setup import create_freight_carrier_settings_for_tests


@pytest.mark.order(30)
def test_get_freight_carrier_settings_none_when_missing():
	assert get_freight_carrier_settings("No Such Company", "No Such Supplier") is None


@pytest.mark.order(31)
def test_get_freight_carrier_settings_resolves_doc():
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
		# Restore seed FCS rows deleted at start; billing and LTL tests run later in the session.
		create_freight_carrier_settings_for_tests(frappe._dict(company=company))
