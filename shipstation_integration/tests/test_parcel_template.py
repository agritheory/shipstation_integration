# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

import json
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest

from shipstation_integration.shipstation_integration.overrides.shipment_parcel_template import (
	sync_parcel_template,
)


def get_parcel_template():
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


@pytest.mark.order(70)
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


@pytest.mark.order(71)
@pytest.mark.parametrize(
	("field_mode", "expected_substr"),
	[
		("missing_dimensions", "Package dimensions are required"),
		("missing_package_code", "Package Code is required"),
	],
)
def test_sync_validation_errors(field_mode: str, expected_substr: str, monkeypatch):
	doc = get_parcel_template()
	try:
		if field_mode == "missing_dimensions":
			doc.length = 0
		else:
			doc.package_code = ""
		doc.save()
		with pytest.raises(Exception) as exc_info:
			sync_parcel_template(doc.name)
		assert expected_substr in str(exc_info.value)
	finally:
		cleanup_parcel_template(doc)


@pytest.mark.order(72)
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
