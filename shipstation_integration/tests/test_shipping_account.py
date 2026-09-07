# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from beam.tests.fixtures import customers

from shipstation_integration.api.carriers import get_shipping_accounts
from shipstation_integration.api.labels import get_third_party_billing_options


TEST_CUSTOMER = customers[1]
TEST_CARRIER = "USPS"
TEST_SERVICE = "Priority Mail"


def save_customer_shipping_accounts(customer_name):
	customer = frappe.get_doc("Customer", customer_name)
	return [row.as_dict() for row in customer.shipping_accounts]


def restore_customer_shipping_accounts(customer_name, rows):
	customer = frappe.get_doc("Customer", customer_name)
	customer.set("shipping_accounts", rows)
	customer.save()


def add_shipping_account_without_number(customer_name, carrier, carrier_service):
	customer = frappe.get_doc("Customer", customer_name)
	customer.append(
		"shipping_accounts",
		{
			"carrier": carrier,
			"carrier_service": carrier_service,
			"enabled": 1,
			"default": 1,
		},
	)
	customer.save()


@pytest.mark.order(84)
def test_get_shipping_accounts_returns_service_without_account_number():
	original = save_customer_shipping_accounts(TEST_CUSTOMER)
	try:
		add_shipping_account_without_number(TEST_CUSTOMER, TEST_CARRIER, TEST_SERVICE)

		accounts = get_shipping_accounts(customer=TEST_CUSTOMER)
		match = next((a for a in accounts if a["carrier"] == TEST_CARRIER), None)
		assert match is not None
		assert not match["shipping_account_number"]
		assert match["carrier_service"] == TEST_SERVICE
	finally:
		restore_customer_shipping_accounts(TEST_CUSTOMER, original)


@pytest.mark.order(85)
def test_third_party_billing_skipped_without_account_number():
	original = save_customer_shipping_accounts(TEST_CUSTOMER)
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()
	original_carrier = ps.carrier
	try:
		add_shipping_account_without_number(TEST_CUSTOMER, TEST_CARRIER, TEST_SERVICE)

		ps.carrier = TEST_CARRIER
		ps.save()

		options = get_third_party_billing_options(ps)
		assert options is None
	finally:
		ps.reload()
		ps.carrier = original_carrier
		ps.save()
		restore_customer_shipping_accounts(TEST_CUSTOMER, original)
