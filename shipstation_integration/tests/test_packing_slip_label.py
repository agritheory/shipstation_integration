# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

import json
from pathlib import Path

import frappe
import pytest

from shipstation_integration.labels import (
	build_shipment_from_packing_slip,
	create_label_for_packing_slip,
	get_existing_label_info,
	get_package_from_packing_slip,
)
from shipstation_integration.rates import get_rates_for_packing_slip


def get_label_response():
	fixtures_dir = Path(frappe.get_app_path("shipstation_integration", "tests", "fixtures"))
	fixture_file = fixtures_dir / "label_response.json"
	with open(fixture_file) as f:
		fixtures = json.load(f)
	return fixtures["captured_responses"][0]["response"]


def get_packing_slip():
	"""
	Return the last draft Packing Slip mutated to have a single parcel with known
	12×9×6 in / 3 lb dimensions.  Saves the original state and returns it alongside
	the doc so each test can restore after use.
	"""
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()

	original = {
		"shipping_address_name": ps.shipping_address_name,
		"dispatch_address_name": ps.dispatch_address_name,
		"carrier": ps.carrier,
		"carrier_service": ps.carrier_service,
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
				"ucc128": row.ucc128,
			}
			for row in ps.items
		],
	}

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
	for saved, row in zip(original["items"], ps.items):
		for field, value in saved.items():
			if field != "name":
				setattr(row, field, value)
	ps.save()


def add_tracking_to_packing_slip(ps):
	"""Set tracking fields on the first Packing Slip Item row."""
	row = ps.items[0]
	frappe.db.set_value(
		"Packing Slip Item",
		row.name,
		{
			"tracking_number": "9400111899223100009999",
			"tracking_url": "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400111899223100009999",
			"label_url": "https://api.shipengine.com/v1/downloads/label-pdf-existing",
			"carrier": "USPS",
		},
	)
	ps.reload()


def clear_tracking_from_packing_slip(ps):
	row = ps.items[0]
	frappe.db.set_value(
		"Packing Slip Item",
		row.name,
		{"tracking_number": None, "tracking_url": None, "label_url": None, "carrier": None},
	)
	ps.reload()


def test_create_label_requires_shipping_address():
	ps, original = get_packing_slip()
	try:
		ps.shipping_address_name = None
		ps.save()
		with pytest.raises(frappe.ValidationError):
			create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
	finally:
		restore_packing_slip(ps, original)


def test_create_label_requires_dispatch_address():
	ps, original = get_packing_slip()
	try:
		ps.dispatch_address_name = None
		ps.save()
		with pytest.raises(frappe.ValidationError):
			create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
	finally:
		restore_packing_slip(ps, original)


def test_create_label_requires_carrier_or_rate():
	ps, original = get_packing_slip()
	try:
		with pytest.raises(frappe.ValidationError):
			create_label_for_packing_slip(ps.name)
	finally:
		restore_packing_slip(ps, original)


def test_build_shipment_from_packing_slip():
	ps, original = get_packing_slip()
	try:
		shipment = build_shipment_from_packing_slip(ps, "se-123", "usps_priority_mail", 1)

		assert shipment["carrier_id"] == "se-123"
		assert shipment["service_code"] == "usps_priority_mail"
		assert "ship_to" in shipment
		assert "ship_from" in shipment
		assert "packages" in shipment
		assert len(shipment["packages"]) == 1
		assert shipment["ship_to"]["city_locality"] == "Boston"
		assert shipment["ship_to"]["state_province"] == "MA"
		assert shipment["ship_from"]["city_locality"] == "Chelsea"
		assert shipment["ship_from"]["state_province"] == "MA"
		pkg = shipment["packages"][0]
		assert pkg["weight"]["value"] == 3
		assert pkg["weight"]["unit"] == "pound"
		assert pkg["dimensions"]["length"] == 12
		assert pkg["dimensions"]["width"] == 9
		assert pkg["dimensions"]["height"] == 6
		assert pkg["dimensions"]["unit"] == "inch"
	finally:
		restore_packing_slip(ps, original)


def test_create_label_with_carrier_and_service(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_shipment.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(
			ps.name, carrier_id="se-123", service_code="usps_priority_mail"
		)
		assert results[0]["tracking_number"] == "9400111899223100001234"
		assert results[0]["label_id"] == "se-test-label-123"
		assert results[0]["carrier_code"] == "usps"
	finally:
		restore_packing_slip(ps, original)


def test_create_label_with_rate_id(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_rate_id.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(ps.name, rate_id="se-rate-123")
		assert results[0]["tracking_number"] == "9400111899223100001234"
		assert results[0]["label_id"] == "se-test-label-123"
	finally:
		restore_packing_slip(ps, original)


def test_tracking_url_built_for_usps(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_shipment.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.labels.download_and_attach_label", lambda *a, **kw: None
		)

		create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
		ps.reload()
		item = ps.items[0]
		assert item.tracking_url
		assert "usps" in item.tracking_url.lower()
		assert "9400111899223100001234" in item.tracking_url
	finally:
		restore_packing_slip(ps, original)


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
	finally:
		restore_packing_slip(ps, original)


def test_get_rates_missing_parcel_number():
	ps, original = get_packing_slip()
	try:
		for row in ps.items:
			row.parcel_number = 0
		ps.save()
		with pytest.raises(frappe.ValidationError):
			get_rates_for_packing_slip(ps.name)
	finally:
		restore_packing_slip(ps, original)


def test_get_package_from_packing_slip():
	ps, original = get_packing_slip()
	try:
		package = get_package_from_packing_slip(ps)
		assert package is not None
		assert "weight" in package
		assert "value" in package["weight"]
		assert "unit" in package["weight"]
		assert package["weight"]["unit"] in ("pound", "ounce", "kilogram", "gram")
		assert "dimensions" in package
		assert package["dimensions"]["length"] > 0
		assert package["dimensions"]["width"] > 0
		assert package["dimensions"]["height"] > 0
		assert package["dimensions"]["unit"] in ("inch", "centimeter")
	finally:
		restore_packing_slip(ps, original)


def test_get_existing_label_info_returns_none_without_tracking():
	ps, original = get_packing_slip()
	try:
		for row in ps.items:
			assert not row.tracking_number
		assert get_existing_label_info(ps) is None
	finally:
		restore_packing_slip(ps, original)


def test_get_existing_label_info_returns_data_when_tracking_exists():
	ps, original = get_packing_slip()
	try:
		add_tracking_to_packing_slip(ps)
		info = get_existing_label_info(ps)

		assert info is not None
		assert info["tracking_number"] == "9400111899223100009999"
		assert "usps" in info["tracking_url"].lower()
		assert "9400111899223100009999" in info["tracking_url"]
		assert info["label_url"]
		assert info["carrier"] == "USPS"
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


def test_create_label_blocked_when_tracking_exists():
	ps, original = get_packing_slip()
	try:
		add_tracking_to_packing_slip(ps)
		with pytest.raises(frappe.DuplicateEntryError):
			create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


def test_create_label_force_repurchases_when_tracking_exists(
	monkeypatch, shipstation_api_client_mock
):
	ps, original = get_packing_slip()
	try:
		add_tracking_to_packing_slip(ps)
		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_shipment.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.labels.download_and_attach_label", lambda *a, **kw: None
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
