# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

"""
Tests for Packing Slip shipping label generation using ShipStation API v2.

These tests use real Frappe documents (Sales Order, Delivery Note, Packing Slip)
created by the test setup, with only the ShipStation API response mocked.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
import pytest


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
	return frappe.get_last_doc("Packing Slip")


class TestPackingSlipLabelValidation:
	"""Validation tests for label creation from Packing Slip."""

	def test_create_label_requires_shipping_address(self, packing_slip):
		"""Test that shipping address is required."""
		from shipstation_integration.labels import create_label_for_packing_slip

		# Clear shipping address
		packing_slip.shipping_address_name = None
		packing_slip.save()

		# Should throw
		with pytest.raises(frappe.ValidationError):
			create_label_for_packing_slip(
				packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
			)

	def test_create_label_requires_dispatch_address(self, packing_slip):
		"""Test that dispatch (ship-from) address is required."""
		from shipstation_integration.labels import create_label_for_packing_slip

		# Clear dispatch address
		packing_slip.dispatch_address_name = None
		packing_slip.save()

		# Should throw
		with pytest.raises(frappe.ValidationError):
			create_label_for_packing_slip(
				packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
			)

	def test_create_label_requires_carrier_or_rate(self, packing_slip):
		"""Test that either rate_id or carrier_id+service_code is required."""
		from shipstation_integration.labels import create_label_for_packing_slip

		# Call with no rate_id and no carrier_id/service_code
		with pytest.raises(frappe.ValidationError):
			create_label_for_packing_slip(packing_slip.name)


class TestPackingSlipShipmentBuilding:
	"""Test shipment data building from Packing Slip."""

	def test_build_shipment_from_packing_slip(self, packing_slip):
		"""Test building shipment data from real Packing Slip."""
		from shipstation_integration.labels import _build_shipment_from_packing_slip

		shipment = _build_shipment_from_packing_slip(packing_slip, "se-123", "usps_priority_mail")

		# Verify structure
		assert shipment["carrier_id"] == "se-123"
		assert shipment["service_code"] == "usps_priority_mail"
		assert "ship_to" in shipment
		assert "ship_from" in shipment
		assert "packages" in shipment
		assert len(shipment["packages"]) == 1

		# Verify ship_to address (Portland, ME)
		assert shipment["ship_to"]["city_locality"] == "Portland"
		assert shipment["ship_to"]["state_province"] == "ME"

		# Verify ship_from address (Chelsea, MA)
		assert shipment["ship_from"]["city_locality"] == "Chelsea"
		assert shipment["ship_from"]["state_province"] == "MA"

		# Verify package dimensions
		pkg = shipment["packages"][0]
		assert pkg["weight"]["value"] == 3
		assert pkg["weight"]["unit"] == "pound"
		assert pkg["dimensions"]["length"] == 12
		assert pkg["dimensions"]["width"] == 9
		assert pkg["dimensions"]["height"] == 6
		assert pkg["dimensions"]["unit"] == "inch"


class TestPackingSlipLabelCreation:
	"""Test label creation with mocked API."""

	def test_create_label_with_carrier_and_service(self, packing_slip, label_response):
		"""Test label creation using carrier_id and service_code."""
		from shipstation_integration.labels import create_label_for_packing_slip

		# Mock the settings and API client
		with patch("shipstation_integration.labels.get_shipstation_settings") as mock_settings_fn:
			mock_settings = MagicMock()
			mock_client = MagicMock()
			mock_client.create_label_from_shipment.return_value = label_response
			mock_settings.shipstation_api_client.return_value = mock_client
			mock_settings_fn.return_value = mock_settings

			# Mock label download
			with patch("shipstation_integration.labels._download_and_attach_label") as mock_download:
				mock_download.return_value = None

				# Create label
				result = create_label_for_packing_slip(
					packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
				)

				# Verify result
				assert result["tracking_number"] == "9400111899223100001234"
				assert result["label_id"] == "se-test-label-123"
				assert result["carrier_code"] == "usps"

	def test_create_label_with_rate_id(self, packing_slip, label_response):
		"""Test label creation using rate_id."""
		from shipstation_integration.labels import create_label_for_packing_slip

		# Mock the settings and API client
		with patch("shipstation_integration.labels.get_shipstation_settings") as mock_settings_fn:
			mock_settings = MagicMock()
			mock_client = MagicMock()
			mock_client.create_label_from_rate_id.return_value = label_response
			mock_settings.shipstation_api_client.return_value = mock_client
			mock_settings_fn.return_value = mock_settings

			# Mock label download
			with patch("shipstation_integration.labels._download_and_attach_label") as mock_download:
				mock_download.return_value = None

				# Create label using rate_id
				result = create_label_for_packing_slip(packing_slip.name, rate_id="se-rate-123")

				# Verify result
				assert result["tracking_number"] == "9400111899223100001234"
				assert result["label_id"] == "se-test-label-123"


class TestTrackingURL:
	"""Test tracking URL generation."""

	def test_tracking_url_built_for_usps(self, packing_slip, label_response):
		"""Test that tracking URL is built correctly for USPS."""
		from shipstation_integration.labels import create_label_for_packing_slip

		# Mock the settings and API client
		with patch("shipstation_integration.labels.get_shipstation_settings") as mock_settings_fn:
			mock_settings = MagicMock()
			mock_client = MagicMock()
			mock_client.create_label_from_shipment.return_value = label_response
			mock_settings.shipstation_api_client.return_value = mock_client
			mock_settings_fn.return_value = mock_settings

			# Mock label download
			with patch("shipstation_integration.labels._download_and_attach_label") as mock_download:
				mock_download.return_value = None

				# Create label
				create_label_for_packing_slip(
					packing_slip.name, carrier_id="se-123", service_code="usps_priority_mail"
				)

				# Reload and check tracking URL
				packing_slip.reload()
				parcel = packing_slip.parcel_dimensions[0]
				assert parcel.tracking_url
				assert "usps" in parcel.tracking_url.lower()
				assert "9400111899223100001234" in parcel.tracking_url


class TestPackingSlipRates:
	"""Test rate shopping from Packing Slip."""

	def test_get_rates_for_packing_slip(self, packing_slip):
		"""Test getting rates for a Packing Slip."""
		from shipstation_integration.rates import get_rates_for_packing_slip

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

		# Mock the settings and API client
		with patch("shipstation_integration.rates.get_shipstation_settings") as mock_settings_fn:
			mock_settings = MagicMock()
			mock_client = MagicMock()
			mock_client.get_rates_from_shipment.return_value = rates_response
			mock_settings.shipstation_api_client.return_value = mock_client
			mock_settings_fn.return_value = mock_settings

			# Get rates
			rates = get_rates_for_packing_slip(packing_slip.name)

			# Verify
			assert len(rates) > 0
			assert rates[0]["rate_id"] == "se-rate-1"
			assert rates[0]["carrier_code"] == "usps"

	def test_get_rates_missing_parcel_dimensions(self, packing_slip):
		"""Test that rates require parcel dimensions."""
		from shipstation_integration.rates import get_rates_for_packing_slip

		# Remove parcel dimensions
		packing_slip.parcel_dimensions = []
		packing_slip.save()

		# Should throw
		with pytest.raises(frappe.ValidationError):
			get_rates_for_packing_slip(packing_slip.name)


class TestPackageExtraction:
	"""Test package extraction from Packing Slip."""

	def test_get_package_from_packing_slip(self, packing_slip):
		"""Test extracting package data from Packing Slip."""
		from shipstation_integration.rates import get_package_from_packing_slip

		package = get_package_from_packing_slip(packing_slip)

		# Verify package data
		assert package is not None
		assert package["weight"]["value"] == 3
		assert package["weight"]["unit"] == "pound"
		assert package["dimensions"]["length"] == 12
		assert package["dimensions"]["width"] == 9
		assert package["dimensions"]["height"] == 6
		assert package["dimensions"]["unit"] == "inch"
