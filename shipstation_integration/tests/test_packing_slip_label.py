# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

"""
Tests for Packing Slip shipping label generation using ShipStation API v2.

These tests use real Frappe documents (Sales Order, Delivery Note, Packing Slip)
created by the test setup, with only the ShipStation API response mocked.
"""

import json
from pathlib import Path
from unittest.mock import patch

import frappe
import pytest

from shipstation_integration.labels import (
	create_label_for_packing_slip,
	get_package_from_packing_slip,
)
from shipstation_integration.rates import get_rates_for_packing_slip


@pytest.fixture
def label_response():
	"""Load the captured ShipEngine label response."""
	fixtures_dir = Path(frappe.get_app_path("shipstation_integration", "tests", "fixtures"))
	fixture_file = fixtures_dir / "label_response.json"
	with open(fixture_file) as f:
		fixtures = json.load(f)
	return fixtures["captured_responses"][0]["response"]


@pytest.fixture
def packing_slip():
	"""Get the Packing Slip created by test setup."""
	ps = frappe.get_last_doc("Packing Slip")
	ps.reload()

	original_shipping = ps.shipping_address_name
	original_dispatch = ps.dispatch_address_name
	original_carrier = ps.carrier
	original_service = ps.carrier_service
	original_item_parcels = [
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
	]

	yield ps

	ps.reload()
	ps.shipping_address_name = original_shipping
	ps.dispatch_address_name = original_dispatch
	ps.carrier = original_carrier
	ps.carrier_service = original_service
	for saved, row in zip(original_item_parcels, ps.items):
		for field, value in saved.items():
			if field != "name":
				setattr(row, field, value)
	ps.save()


def test_create_label_requires_shipping_address(packing_slip):
	"""Test that shipping address is required."""
	packing_slip.shipping_address_name = None
	packing_slip.save()
	with pytest.raises(frappe.ValidationError):
		create_label_for_packing_slip(
			packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
		)


def test_create_label_requires_dispatch_address(packing_slip):
	"""Test that dispatch (ship-from) address is required."""
	packing_slip.dispatch_address_name = None
	packing_slip.save()
	with pytest.raises(frappe.ValidationError):
		create_label_for_packing_slip(
			packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
		)


def test_create_label_requires_carrier_or_rate(packing_slip):
	"""Test that either rate_id or carrier_id+service_code is required."""
	with pytest.raises(frappe.ValidationError):
		create_label_for_packing_slip(packing_slip.name)


def test_build_shipment_from_packing_slip(packing_slip):
	"""Test building shipment data from real Packing Slip."""
	from shipstation_integration.labels import _build_shipment_from_packing_slip

	shipment = _build_shipment_from_packing_slip(packing_slip, "se-123", "usps_priority_mail")

	assert shipment["carrier_id"] == "se-123"
	assert shipment["service_code"] == "usps_priority_mail"
	assert "ship_to" in shipment
	assert "ship_from" in shipment
	assert "packages" in shipment
	assert len(shipment["packages"]) == 1
	assert shipment["ship_to"]["city_locality"] == "Portland"
	assert shipment["ship_to"]["state_province"] == "ME"
	assert shipment["ship_from"]["city_locality"] == "Chelsea"
	assert shipment["ship_from"]["state_province"] == "MA"
	pkg = shipment["packages"][0]
	assert pkg["weight"]["value"] == 3
	assert pkg["weight"]["unit"] == "pound"
	assert pkg["dimensions"]["length"] == 12
	assert pkg["dimensions"]["width"] == 9
	assert pkg["dimensions"]["height"] == 6
	assert pkg["dimensions"]["unit"] == "inch"


def test_create_label_with_carrier_and_service(
	packing_slip, label_response, mock_settings_with_client
):
	"""Test label creation using carrier_id and service_code."""
	mock_client = mock_settings_with_client.shipstation_api_client.return_value
	mock_client.create_label_from_shipment.return_value = label_response

	with patch("shipstation_integration.labels._download_and_attach_label") as mock_download:
		mock_download.return_value = None
		result = create_label_for_packing_slip(
			packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
		)
		assert result["tracking_number"] == "9400111899223100001234"
		assert result["label_id"] == "se-test-label-123"
		assert result["carrier_code"] == "usps"


def test_create_label_with_rate_id(packing_slip, label_response, mock_settings_with_client):
	"""Test label creation using rate_id."""
	mock_client = mock_settings_with_client.shipstation_api_client.return_value
	mock_client.create_label_from_rate_id.return_value = label_response
	with patch("shipstation_integration.labels._download_and_attach_label") as mock_download:
		mock_download.return_value = None
		result = create_label_for_packing_slip(packing_slip.name, rate_id="se-rate-123")
		assert result["tracking_number"] == "9400111899223100001234"
		assert result["label_id"] == "se-test-label-123"


def test_tracking_url_built_for_usps(packing_slip, label_response, mock_settings_with_client):
	"""Test that tracking URL is written to items and is correct for USPS."""
	mock_client = mock_settings_with_client.shipstation_api_client.return_value
	mock_client.create_label_from_shipment.return_value = label_response
	with patch("shipstation_integration.labels._download_and_attach_label") as mock_download:
		mock_download.return_value = None
		create_label_for_packing_slip(
			packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
		)
		packing_slip.reload()
		item = packing_slip.items[0]
		assert item.tracking_url
		assert "usps" in item.tracking_url.lower()
		assert "9400111899223100001234" in item.tracking_url


def test_get_rates_for_packing_slip(packing_slip, mock_settings_with_client):
	"""Test getting rates for a Packing Slip."""
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

	mock_client = mock_settings_with_client.shipstation_api_client.return_value
	mock_client.get_rates_from_shipment.return_value = rates_response
	rates = get_rates_for_packing_slip(packing_slip.name)
	assert len(rates) > 0
	assert rates[0]["rate_id"] == "se-rate-1"
	assert rates[0]["carrier_code"] == "usps"


def test_get_rates_missing_parcel_number(packing_slip):
	"""Test that rates require items with a parcel number set."""
	for row in packing_slip.items:
		row.parcel_number = 0
	packing_slip.save()
	with pytest.raises(frappe.ValidationError):
		get_rates_for_packing_slip(packing_slip.name)


def test_get_package_from_packing_slip(packing_slip):
	"""Test extracting package data from Packing Slip."""
	package = get_package_from_packing_slip(packing_slip)
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
