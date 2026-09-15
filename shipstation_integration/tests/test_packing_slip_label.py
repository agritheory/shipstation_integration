# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

import json
from pathlib import Path

import frappe
import pytest

from shipstation_integration.api.labels import (
	build_shipment_from_packing_slip,
	create_label_for_packing_slip,
	void_label_for_packing_slip,
)
from shipstation_integration.api.rates import (
	get_package_from_packing_slip,
	get_rates_for_packing_slip,
	mark_matching_rate,
)
from shipstation_integration.label_options import billing_options_from_freight_terms
from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
	get_first_sales_order_from_packing_slip,
)


def get_label_response():
	fixtures_dir = Path(frappe.get_app_path("shipstation_integration", "tests", "fixtures"))
	fixture_file = fixtures_dir / "label_response.json"
	with open(fixture_file) as f:
		fixtures = json.load(f)
	return fixtures["captured_responses"][0]["response"]


def packing_slip_snapshot(ps):
	return {
		"shipping_address_name": ps.shipping_address_name,
		"dispatch_address_name": ps.dispatch_address_name,
		"carrier": ps.carrier,
		"carrier_service": ps.carrier_service,
		"gross_weight_pkg": ps.gross_weight_pkg,
		"items": [
			{
				"name": row.name,
				"parcel_number": row.parcel_number,
				"parcel_length": row.parcel_length,
				"parcel_width": row.parcel_width,
				"parcel_height": row.parcel_height,
				"dimension_uom": row.dimension_uom,
				"parcel_weight": row.parcel_weight,
				"parcel_weight_uom": row.parcel_weight_uom,
				"tracking_number": row.tracking_number,
				"tracking_url": row.tracking_url,
				"label_url": row.label_url,
				"label_id": row.get("label_id"),
				"ucc128": row.ucc128,
			}
			for row in ps.items
		],
	}


def get_packing_slip():
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()

	original = packing_slip_snapshot(ps)

	for row in ps.items:
		row.parcel_number = 1
		row.parcel_length = 12
		row.parcel_width = 9
		row.parcel_height = 6
		row.dimension_uom = "Inch"
		row.parcel_weight = 3
		row.parcel_weight_uom = "Pound"
	ps.save()
	ps.reload()

	return ps, original


def restore_packing_slip(ps, original):
	ps.reload()
	ps.shipping_address_name = original["shipping_address_name"]
	ps.dispatch_address_name = original["dispatch_address_name"]
	ps.carrier = original["carrier"]
	ps.carrier_service = original["carrier_service"]
	ps.gross_weight_pkg = original.get("gross_weight_pkg")
	for saved, row in zip(original["items"], ps.items):
		for field, value in saved.items():
			if field != "name":
				setattr(row, field, value)
	ps.save()


def add_tracking_to_packing_slip(ps):
	row = ps.items[0]
	frappe.db.set_value(
		"Packing Slip Item",
		row.name,
		{
			"tracking_number": "9400111899223100009999",
			"tracking_url": "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400111899223100009999",
			"label_url": "https://api.shipengine.com/v1/downloads/label-pdf-existing",
			"label_id": "se-test-label-existing",
			"carrier": "USPS",
		},
	)
	ps.reload()


def clear_tracking_from_packing_slip(ps):
	row = ps.items[0]
	frappe.db.set_value(
		"Packing Slip Item",
		row.name,
		{
			"tracking_number": None,
			"tracking_url": None,
			"label_url": None,
			"label_id": None,
			"carrier": None,
		},
	)
	ps.reload()


def get_multi_parcel_packing_slip():
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()
	return ps, packing_slip_snapshot(ps)


def add_shipping_account_with_number(customer_name, carrier, carrier_service, account_number):
	customer = frappe.get_doc("Customer", customer_name)
	customer.append(
		"shipping_accounts",
		{
			"carrier": carrier,
			"carrier_service": carrier_service,
			"shipping_account_number": account_number,
			"enabled": 1,
			"default": 1,
		},
	)
	customer.save()


def save_customer_shipping_accounts(customer_name):
	customer = frappe.get_doc("Customer", customer_name)
	return [row.as_dict() for row in customer.shipping_accounts]


def restore_customer_shipping_accounts(customer_name, rows):
	customer = frappe.get_doc("Customer", customer_name)
	customer.set("shipping_accounts", rows)
	customer.save()


@pytest.mark.order(80)
def test_create_label_happy_path(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_shipment.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(
			ps.name, carrier_id="se-123", service_code="usps_priority_mail"
		)
		assert results[0]["tracking_number"] == "9400111899223100001234"
		assert results[0]["label_id"] == "se-test-label-123"
		assert results[0]["carrier_code"] == "usps"

		ps.reload()
		item = ps.items[0]
		assert item.tracking_url
		assert "usps" in item.tracking_url.lower()
		assert "9400111899223100001234" in item.tracking_url
		assert item.label_id == "se-test-label-123"
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(81)
@pytest.mark.parametrize(
	"mutate",
	("clear_shipping", "clear_dispatch", "no_carrier"),
)
def test_create_label_validation_errors(mutate):
	ps, original = get_packing_slip()
	try:
		if mutate == "clear_shipping":
			ps.shipping_address_name = None
			ps.save()
		elif mutate == "clear_dispatch":
			ps.dispatch_address_name = None
			ps.save()

		with pytest.raises(frappe.ValidationError):
			if mutate == "no_carrier":
				create_label_for_packing_slip(ps.name)
			else:
				create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(82)
def test_get_rates_for_packing_slip(shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		rates_response = {
			"rates": [
				{
					"rate_id": "se-rate-1",
					"carrier_id": "se-usps",
					"carrier_code": "usps",
					"carrier_friendly_name": "USPS",
					"service_code": "usps_priority_mail",
					"service_type": "Priority Mail",
					"shipping_amount": {"currency": "usd", "amount": 7.95},
					"delivery_days": 1,
					"trackable": True,
				}
			]
		}
		api_client = shipstation_api_client_mock
		api_client.get_rates_from_shipment.return_value = rates_response

		rates = get_rates_for_packing_slip(ps.name)
		assert len(rates) > 0
		assert rates[0]["rate_id"] == "se-rate-1"
		assert rates[0]["carrier_code"] == "usps"
		assert rates[0].get("selected") is True
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(83)
def test_existing_label_blocks_unless_forced(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		add_tracking_to_packing_slip(ps)
		with pytest.raises(frappe.DuplicateEntryError):
			create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")

		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_shipment.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(
			ps.name, carrier_id="se-123", service_code="usps_priority_mail", force=True
		)

		assert results[0]["tracking_number"] == "9400111899223100001234"
		assert results[0]["label_id"] == "se-test-label-123"
		api_client.create_label_from_shipment.assert_called_once()
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


@pytest.mark.order(92)
@pytest.mark.parametrize("missing", ("weight", "length", "gross_fallback"))
def test_package_requires_weight_and_dimensions(missing):
	ps, original = get_multi_parcel_packing_slip()
	try:
		if missing == "gross_fallback":
			for row in ps.items:
				row.parcel_number = None
			ps.gross_weight_pkg = 10
			ps.save()
			ps.reload()
			assert get_package_from_packing_slip(ps) is None
			return

		row = next(item for item in ps.items if item.parcel_number == 1)
		if missing == "weight":
			row.parcel_weight = 0
			match = "missing weight"
		else:
			row.parcel_length = 0
			match = "missing dimensions"
		ps.save()
		ps.reload()
		with pytest.raises(frappe.ValidationError, match=match):
			get_package_from_packing_slip(ps, parcel_number=1)
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(93)
def test_package_uses_requested_parcel_not_first():
	ps, original = get_multi_parcel_packing_slip()
	try:
		row_two = next(item for item in ps.items if item.parcel_number == 2)
		row_two.parcel_weight = 5
		row_two.parcel_length = 10
		row_two.parcel_width = 8
		row_two.parcel_height = 6
		ps.save()
		ps.reload()

		package_two = get_package_from_packing_slip(ps, parcel_number=2)
		assert package_two["weight"]["value"] == 5
		assert package_two["dimensions"]["length"] == 10

		package_one = get_package_from_packing_slip(ps, parcel_number=1)
		assert package_one["weight"]["value"] == 3
		assert package_one["dimensions"]["length"] == 12
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(94)
def test_shipment_payload_billing_follows_freight_terms():
	from beam.tests.fixtures import customers

	ps, original = get_packing_slip()
	account_rows = save_customer_shipping_accounts(customers[1])
	try:
		shipment = build_shipment_from_packing_slip(ps, "se-123", "usps_priority_mail", 1)
		assert "advanced_options" not in shipment

		add_shipping_account_with_number(customers[1], "USPS", "Priority Mail", "BD-COLLECT-441")
		assert billing_options_from_freight_terms(ps, "Prepaid") is None
		assert billing_options_from_freight_terms(ps, None) is None

		collect = billing_options_from_freight_terms(ps, "Collect")
		assert collect["bill_to_party"] == "third_party"
		assert collect["bill_to_account"] == "BD-COLLECT-441"
	finally:
		restore_customer_shipping_accounts(customers[1], account_rows)
		restore_packing_slip(ps, original)


@pytest.mark.order(95)
def test_label_reference_defaults_to_sales_order_name():
	ps, original = get_packing_slip()
	try:
		so_name = get_first_sales_order_from_packing_slip(ps)
		assert so_name
		shipment = build_shipment_from_packing_slip(ps, "se-123", "usps_priority_mail", 1)
		assert shipment["external_order_id"] == so_name
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(96)
def test_create_label_persists_label_id_on_item(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	existing_tracking_numbers = set(frappe.get_all("Tracking Number", pluck="name"))
	try:
		label_response = get_label_response()
		shipstation_api_client_mock.create_label_from_shipment.return_value = label_response
		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
		ps.reload()
		assert ps.items[0].label_id == "se-test-label-123"
		assert ps.items[0].tracking_number == "9400111899223100001234"
		assert set(frappe.get_all("Tracking Number", pluck="name")) == existing_tracking_numbers
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


@pytest.mark.order(97)
def test_void_label_clears_item_tracking(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		label_response = get_label_response()
		shipstation_api_client_mock.create_label_from_shipment.return_value = label_response
		shipstation_api_client_mock.void_label.return_value = {"approved": True, "message": "Voided"}
		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
		result = void_label_for_packing_slip(ps.name, 1)
		assert result["approved"] is True
		shipstation_api_client_mock.void_label.assert_called_once_with(label_id="se-test-label-123")

		ps.reload()
		assert not ps.items[0].tracking_number
		assert not ps.items[0].label_url
		assert not ps.items[0].label_id
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


@pytest.mark.order(98)
def test_void_label_requires_label_id_on_parcel():
	ps, original = get_packing_slip()
	try:
		with pytest.raises(frappe.ValidationError, match="no label"):
			void_label_for_packing_slip(ps.name, 1)
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(99)
def test_mark_matching_rate_selects_only_exact_single_match():
	priority = {
		"rate_id": "se-rate-1",
		"service_code": "usps_priority_mail",
		"service_type": "Priority Mail",
	}
	ground = {
		"rate_id": "se-rate-2",
		"service_code": "usps_ground",
		"service_type": "Ground",
	}
	duplicate = {
		"rate_id": "se-rate-3",
		"service_code": "usps_priority_mail",
		"service_type": "Priority Mail",
	}

	one = mark_matching_rate([priority, ground], "usps_priority_mail")
	assert one[0].get("selected") is True
	assert "selected" not in one[1]

	none = mark_matching_rate([priority, ground], "fedex_home_delivery")
	assert all("selected" not in rate for rate in none)

	two = mark_matching_rate([priority, duplicate], "usps_priority_mail")
	assert all("selected" not in rate for rate in two)
