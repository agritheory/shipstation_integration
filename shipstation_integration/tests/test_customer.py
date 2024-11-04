# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from shipstation.models import ShipStationAddress, ShipStationOrder

from shipstation_integration.customer import create_address, create_customer, update_address
from shipstation_integration.tests.setup import create_test_data


@pytest.fixture(autouse=True)
def setup():
	create_test_data()


@pytest.fixture
def customer():
	customer = frappe.new_doc("Customer")
	customer.update(
		{
			"customer_name": "John Doe",
			"customer_group": "All Customer Groups",
			"territory": "All Territories",
			"customer_type": "Individual",
			"customer_primary_contact": "John Doe",
			"customer_primary_email": "john.doe@example.com",
			"customer_primary_mobile": "0987654321",
			"customer_primary_phone": "0987654321",
		}
	)
	return customer


@pytest.fixture
def address1():
	return ShipStationAddress(
		name="John Doe",
		street1="123 Test St",
		city="Test City",
		state="Test State",
		postal_code="54321",
		country="US",
		phone="0987654321",
	)


@pytest.fixture
def address2():
	return ShipStationAddress(
		name="John Doe",
		street1="456 Test St",
		city="Test City",
		state="Test State",
		postal_code="54321",
		country="US",
		phone="0987654321",
	)


@pytest.fixture
def order(address1):
	return ShipStationOrder(
		bill_to=address1,
		customer_email="john.doe@example.com",
		customer_id="12345",
		ship_to=address1,
		order_id="54321",
		order_number="ORD123",
		ship_date="2023-10-10",
		customer_notes="Test note",
		internal_notes="Internal test note",
	)


def test_create_address_error(address1, customer):
	address = create_address(address1, customer.name, "john.doe@amazon.com", "Shipping")
	assert address.is_new()  # assert that address is unsaved
	error_log = frappe.get_last_doc("Error Log")
	assert error_log.error == "Address Title is mandatory."


def test_create_update_address(address1, address2, customer):
	customer.save()

	address = create_address(address1, customer.name, "john.doe@amazon.com", "Shipping")
	assert address.is_new() is None
	assert address.address_type == "Shipping"
	assert address.address_line1 == "123 Test St"
	assert address.address_line2 == None
	assert address.address_line3 == None
	assert address.city == "Test City"
	assert address.state == "Test State"
	assert address.pincode == "54321"
	assert address.country == "United States"
	assert address.phone == "0987654321"
	assert address.email == "john.doe@amazon.com"

	address_new = update_address(address2, address.name, "john.doe@amazon.com", "Shipping")
	assert address_new.is_new() is None
	assert address_new.address_type == "Shipping"
	assert address_new.address_line1 == "456 Test St"
	assert address_new.address_line2 == None
	assert address_new.address_line3 == None
	assert address_new.city == "Test City"
	assert address_new.state == "Test State"
	assert address_new.pincode == "54321"
	assert address_new.country == "United States"
	assert address_new.phone == "0987654321"
	assert address_new.email == "john.doe@amazon.com"


def test_create_order(order):
	customer = create_customer(order)
	assert customer.is_new() is None
	assert customer.customer_name == "john.doe@example.com"

	customer_dupe = create_customer(order)
	assert customer_dupe.name == customer.name
