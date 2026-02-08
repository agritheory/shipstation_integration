# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

"""
Tests for Shipment Parcel Template customizations.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
import pytest


@pytest.fixture
def test_parcel_template():
	"""Create a test Shipment Parcel Template."""
	if frappe.db.exists("Shipment Parcel Template", "Test Box"):
		doc = frappe.get_doc("Shipment Parcel Template", "Test Box")
	else:
		doc = frappe.new_doc("Shipment Parcel Template")
		doc.parcel_template_name = "Test Box"
		doc.length = 30  # cm
		doc.width = 20  # cm
		doc.height = 15  # cm
		doc.weight = 2  # kg
		doc.package_code = "test_box_001"
		doc.insert()

	yield doc

	# Cleanup
	if frappe.db.exists("Shipment Parcel Template", doc.name):
		frappe.delete_doc("Shipment Parcel Template", doc.name, force=True)


@pytest.fixture
def fixtures_dir():
	"""Get the fixtures directory."""
	return Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_shipstation_response(fixtures_dir):
	"""Load the captured ShipStation API response."""
	fixture_file = fixtures_dir / "shipstation_packages_response.json"
	with open(fixture_file) as f:
		fixtures = json.load(f)

	# Return the first captured response
	return fixtures["captured_responses"][0]["response"]


def test_uom_conversion_length():
	"""Test UOM conversion for length dimension."""
	from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
		get_conversion_factor,
	)

	# Test identity conversion
	assert get_conversion_factor("Centimeter", "Centimeter") == 1

	# Test forward conversion (will need UOM Conversion Factor records)
	# These would be set up in the test fixture data
	# For now, just test the identity case


def test_sync_validation_missing_dimensions(test_parcel_template):
	"""Test that sync_parcel_template validates required dimensions."""
	from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
		sync_parcel_template,
	)

	# Clear dimensions
	test_parcel_template.length = 0
	test_parcel_template.save()

	# Should raise validation error
	with pytest.raises(Exception) as exc_info:
		sync_parcel_template(test_parcel_template.name)

	assert "Package dimensions are required" in str(exc_info.value)


def test_sync_validation_missing_package_code(test_parcel_template):
	"""Test that sync_parcel_template validates required package_code."""
	from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
		sync_parcel_template,
	)

	# Clear package code
	test_parcel_template.package_code = ""
	test_parcel_template.save()

	# Should raise validation error
	with pytest.raises(Exception) as exc_info:
		sync_parcel_template(test_parcel_template.name)

	assert "Package Code is required" in str(exc_info.value)


def test_package_code_custom_prefix(test_parcel_template, mock_shipstation_response):
	"""Test that package_code gets 'custom_' prefix if not already present."""
	from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
		sync_parcel_template,
	)

	# Mock the HTTP call
	with patch("httpx.Client") as mock_client:
		# Setup mock response using captured data
		mock_response = MagicMock()
		mock_response.status_code = mock_shipstation_response["status_code"]
		mock_response.json.return_value = mock_shipstation_response["response_body"]
		mock_client.return_value.__enter__.return_value.post.return_value = mock_response

		# Mock settings
		with patch(
			"shipstation_integration.shipstation_integration.overrides.shipment_parcel_template.get_shipstation_settings"
		) as mock_settings:
			mock_settings_doc = MagicMock()
			mock_settings_doc.get_password.return_value = "fake_api_key"
			mock_settings.return_value = mock_settings_doc

			# Call sync
			sync_parcel_template(test_parcel_template.name)

			# Verify the API was called with custom_ prefix
			call_args = mock_client.return_value.__enter__.return_value.post.call_args
			payload = call_args[1]["json"]

			assert payload["package_code"] == "custom_test_box_001"


def test_sync_parcel_template_success(test_parcel_template, mock_shipstation_response):
	"""Test successful package sync using mocked response."""
	from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
		sync_parcel_template,
	)

	# Mock the HTTP call
	with patch("httpx.Client") as mock_client:
		# Setup mock response
		mock_response = MagicMock()
		mock_response.status_code = mock_shipstation_response["status_code"]
		mock_response.json.return_value = mock_shipstation_response["response_body"]
		mock_client.return_value.__enter__.return_value.post.return_value = mock_response

		# Mock settings
		with patch(
			"shipstation_integration.shipstation_integration.overrides.shipment_parcel_template.get_shipstation_settings"
		) as mock_settings:
			mock_settings_doc = MagicMock()
			mock_settings_doc.get_password.return_value = "fake_api_key"
			mock_settings.return_value = mock_settings_doc

			# Call sync
			sync_parcel_template(test_parcel_template.name)

			# Reload the document to check updated fields
			test_parcel_template.reload()

			# Verify the package_id and package_code were set
			assert (
				test_parcel_template.package_id == mock_shipstation_response["response_body"]["package_id"]
			)
			assert (
				test_parcel_template.package_code == mock_shipstation_response["response_body"]["package_code"]
			)
			assert test_parcel_template.is_package_synced == 1


def test_sync_skipped_when_flag_set(test_parcel_template):
	"""Test that sync is skipped when skip_shipstation_sync is checked."""
	from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
		sync_parcel_template,
	)

	# Set the skip flag
	test_parcel_template.skip_shipstation_sync = 1
	test_parcel_template.save()

	# Should raise validation error
	with pytest.raises(Exception) as exc_info:
		sync_parcel_template(test_parcel_template.name)

	assert "skip ShipStation sync" in str(exc_info.value)


def test_package_code_not_required_when_sync_skipped(test_parcel_template):
	"""Test that package_code is not required when skip_shipstation_sync is checked."""
	# Set the skip flag and clear package code
	test_parcel_template.skip_shipstation_sync = 1
	test_parcel_template.package_code = ""

	# Should be able to save without package_code
	test_parcel_template.save()

	# Verify it saved successfully
	test_parcel_template.reload()
	assert test_parcel_template.skip_shipstation_sync == 1
	assert test_parcel_template.package_code == ""


def test_sync_api_error_handling(test_parcel_template):
	"""Test that API errors are handled properly."""
	from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
		sync_parcel_template,
	)

	# Mock the HTTP call to return an error
	with patch("httpx.Client") as mock_client:
		# Setup mock error response
		mock_response = MagicMock()
		mock_response.status_code = 400
		mock_response.text = "Invalid package dimensions"
		mock_client.return_value.__enter__.return_value.post.return_value = mock_response

		# Mock settings
		with patch(
			"shipstation_integration.shipstation_integration.overrides.shipment_parcel_template.get_shipstation_settings"
		) as mock_settings:
			mock_settings_doc = MagicMock()
			mock_settings_doc.get_password.return_value = "fake_api_key"
			mock_settings.return_value = mock_settings_doc

			# Should raise an error
			with pytest.raises(Exception) as exc_info:
				sync_parcel_template(test_parcel_template.name)

			assert "ShipStation error" in str(exc_info.value)
			assert "400" in str(exc_info.value)
