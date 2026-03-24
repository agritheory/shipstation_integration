# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
import pytest

from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
	get_conversion_factor,
	sync_parcel_template,
)


@pytest.fixture
def test_parcel_template():
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

	if frappe.db.exists("Shipment Parcel Template", doc.name):
		frappe.delete_doc("Shipment Parcel Template", doc.name, force=True)


@pytest.fixture
def mock_shipstation_response():
	fixtures_dir = Path(__file__).parent / "fixtures"
	fixture_file = fixtures_dir / "shipstation_packages_response.json"
	with open(fixture_file) as f:
		fixtures = json.load(f)

	return fixtures["captured_responses"][0]["response"]


def test_uom_conversion_length():
	assert get_conversion_factor("Centimeter", "Centimeter") == 1


def test_sync_validation_missing_dimensions(test_parcel_template):
	test_parcel_template.length = 0
	test_parcel_template.save()
	with pytest.raises(Exception) as exc_info:
		sync_parcel_template(test_parcel_template.name)
	assert "Package dimensions are required" in str(exc_info.value)


def test_sync_validation_missing_package_code(test_parcel_template):
	test_parcel_template.package_code = ""
	test_parcel_template.save()
	with pytest.raises(Exception) as exc_info:
		sync_parcel_template(test_parcel_template.name)
	assert "Package Code is required" in str(exc_info.value)


def test_package_code_custom_prefix(test_parcel_template, mock_shipstation_response):
	with patch("httpx.Client") as mock_client:
		mock_response = MagicMock()
		mock_response.status_code = mock_shipstation_response["status_code"]
		mock_response.json.return_value = mock_shipstation_response["response_body"]
		mock_client.return_value.__enter__.return_value.post.return_value = mock_response
		sync_parcel_template(test_parcel_template.name)
		call_args = mock_client.return_value.__enter__.return_value.post.call_args
		payload = call_args[1]["json"]
		assert payload["package_code"] == "custom_test_box_001"


def test_sync_parcel_template_success(test_parcel_template, mock_shipstation_response):
	with patch("httpx.Client") as mock_client:
		mock_response = MagicMock()
		mock_response.status_code = mock_shipstation_response["status_code"]
		mock_response.json.return_value = mock_shipstation_response["response_body"]
		mock_client.return_value.__enter__.return_value.post.return_value = mock_response
		sync_parcel_template(test_parcel_template.name)
		test_parcel_template.reload()
		assert (
			test_parcel_template.package_id == mock_shipstation_response["response_body"]["package_id"]
		)
		assert (
			test_parcel_template.package_code == mock_shipstation_response["response_body"]["package_code"]
		)
		assert test_parcel_template.is_package_synced == 1


def test_sync_skipped_when_flag_set(test_parcel_template):
	test_parcel_template.skip_shipstation_sync = 1
	test_parcel_template.save()
	with pytest.raises(Exception) as exc_info:
		sync_parcel_template(test_parcel_template.name)
	assert "skip ShipStation sync" in str(exc_info.value)


def test_package_code_not_required_when_sync_skipped(test_parcel_template):
	test_parcel_template.skip_shipstation_sync = 1
	test_parcel_template.package_code = ""
	test_parcel_template.save()
	test_parcel_template.reload()
	assert test_parcel_template.skip_shipstation_sync == 1
	assert test_parcel_template.package_code == ""


def test_sync_api_error_handling(test_parcel_template):
	with patch("httpx.Client") as mock_client:
		mock_response = MagicMock()
		mock_response.status_code = 400
		mock_response.text = "Invalid package dimensions"
		mock_client.return_value.__enter__.return_value.post.return_value = mock_response
		with pytest.raises(Exception) as exc_info:
			sync_parcel_template(test_parcel_template.name)
		assert "ShipStation error" in str(exc_info.value)
		assert "400" in str(exc_info.value)
