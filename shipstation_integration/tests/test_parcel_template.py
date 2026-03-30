# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

import json
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest

from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
	get_conversion_factor,
	sync_parcel_template,
)


def get_parcel_template():
	"""Upsert and return a known-state Shipment Parcel Template for testing."""
	if frappe.db.exists("Shipment Parcel Template", "Test Box"):
		doc = frappe.get_doc("Shipment Parcel Template", "Test Box")
		doc.length = 30
		doc.width = 20
		doc.height = 15
		doc.weight = 2
		doc.package_code = "test_box_001"
		doc.skip_shipstation_sync = 0
		doc.save()
	else:
		doc = frappe.new_doc("Shipment Parcel Template")
		doc.parcel_template_name = "Test Box"
		doc.length = 30
		doc.width = 20
		doc.height = 15
		doc.weight = 2
		doc.package_code = "test_box_001"
		doc.insert()
	doc.reload()
	return doc


def cleanup_parcel_template(doc):
	if frappe.db.exists("Shipment Parcel Template", doc.name):
		frappe.delete_doc("Shipment Parcel Template", doc.name, force=True)


def get_shipstation_response():
	fixtures_dir = Path(__file__).parent / "fixtures"
	fixture_file = fixtures_dir / "shipstation_packages_response.json"
	with open(fixture_file) as f:
		fixtures = json.load(f)
	return fixtures["captured_responses"][0]["response"]


def make_mock_httpx_post(response_data):
	mock_response = MagicMock()
	mock_response.status_code = response_data["status_code"]
	mock_response.json.return_value = response_data["response_body"]
	return mock_response


def test_uom_conversion_length():
	assert get_conversion_factor("Centimeter", "Centimeter") == 1


def test_sync_validation_missing_dimensions(monkeypatch):
	doc = get_parcel_template()
	try:
		doc.length = 0
		doc.save()
		with pytest.raises(Exception) as exc_info:
			sync_parcel_template(doc.name)
		assert "Package dimensions are required" in str(exc_info.value)
	finally:
		cleanup_parcel_template(doc)


def test_sync_validation_missing_package_code(monkeypatch):
	doc = get_parcel_template()
	try:
		doc.package_code = ""
		doc.save()
		with pytest.raises(Exception) as exc_info:
			sync_parcel_template(doc.name)
		assert "Package Code is required" in str(exc_info.value)
	finally:
		cleanup_parcel_template(doc)


def test_package_code_custom_prefix(monkeypatch):
	doc = get_parcel_template()
	try:
		response_data = get_shipstation_response()
		mock_post = make_mock_httpx_post(response_data)

		mock_client = MagicMock()
		mock_client.__enter__ = MagicMock(return_value=mock_client)
		mock_client.__exit__ = MagicMock(return_value=False)
		mock_client.post.return_value = mock_post
		monkeypatch.setattr("httpx.Client", lambda *a, **kw: mock_client)

		sync_parcel_template(doc.name)
		payload = mock_client.post.call_args[1]["json"]
		assert payload["package_code"] == "custom_test_box_001"
	finally:
		cleanup_parcel_template(doc)


def test_sync_parcel_template_success(monkeypatch):
	doc = get_parcel_template()
	try:
		response_data = get_shipstation_response()
		mock_post = make_mock_httpx_post(response_data)

		mock_client = MagicMock()
		mock_client.__enter__ = MagicMock(return_value=mock_client)
		mock_client.__exit__ = MagicMock(return_value=False)
		mock_client.post.return_value = mock_post
		monkeypatch.setattr("httpx.Client", lambda *a, **kw: mock_client)

		sync_parcel_template(doc.name)
		doc.reload()
		assert doc.package_id == response_data["response_body"]["package_id"]
		assert doc.package_code == response_data["response_body"]["package_code"]
		assert doc.is_package_synced == 1
	finally:
		cleanup_parcel_template(doc)


def test_sync_skipped_when_flag_set():
	doc = get_parcel_template()
	try:
		doc.skip_shipstation_sync = 1
		doc.save()
		with pytest.raises(Exception) as exc_info:
			sync_parcel_template(doc.name)
		assert "skip ShipStation sync" in str(exc_info.value)
	finally:
		cleanup_parcel_template(doc)


def test_package_code_not_required_when_sync_skipped():
	doc = get_parcel_template()
	try:
		doc.skip_shipstation_sync = 1
		doc.package_code = ""
		doc.save()
		doc.reload()
		assert doc.skip_shipstation_sync == 1
		assert doc.package_code == ""
	finally:
		cleanup_parcel_template(doc)


def test_sync_api_error_handling(monkeypatch):
	doc = get_parcel_template()
	try:
		mock_response = MagicMock()
		mock_response.status_code = 400
		mock_response.text = "Invalid package dimensions"

		mock_client = MagicMock()
		mock_client.__enter__ = MagicMock(return_value=mock_client)
		mock_client.__exit__ = MagicMock(return_value=False)
		mock_client.post.return_value = mock_response
		monkeypatch.setattr("httpx.Client", lambda *a, **kw: mock_client)

		with pytest.raises(Exception) as exc_info:
			sync_parcel_template(doc.name)
		assert "ShipStation error" in str(exc_info.value)
		assert "400" in str(exc_info.value)
	finally:
		cleanup_parcel_template(doc)
