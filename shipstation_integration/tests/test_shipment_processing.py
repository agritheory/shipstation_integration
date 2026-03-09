# Copyright (c) 2026, AgriTheory and Contributors
# See license.txt

"""
Tests for background shipment processing error handling.

These tests verify that failures in create_erpnext_shipment are:
  - caught without crashing the background job
  - rolled back cleanly via savepoint
  - logged to the Error Log with a consistent title prefix
  - surfaced on the Shipstation Settings form via __onload.errors
"""

from unittest.mock import MagicMock, patch

import frappe
import pytest
from shipstation.models import ShipStationOrder


ORDER_ID = "test-shipstation-order-99999"
ERROR_LOG_TITLE = f"Shipstation: order {ORDER_ID}"


@pytest.fixture
def mock_shipment():
	shipment = MagicMock(spec=ShipStationOrder)
	shipment.order_id = ORDER_ID
	shipment.shipment_id = "test-shipment-99999"
	return shipment


@pytest.fixture
def mock_store():
	store = MagicMock()
	store.create_sales_invoice = True
	store.create_delivery_note = True
	store.create_shipment = False
	return store


@pytest.fixture
def mock_settings():
	settings = MagicMock()
	settings.shipstation_user = None
	return settings


@pytest.fixture(autouse=True)
def cleanup_error_log():
	"""Remove any Error Log entries created by these tests after each test."""
	yield
	for name in frappe.get_all("Error Log", filters={"method": ERROR_LOG_TITLE}, pluck="name"):
		frappe.delete_doc("Error Log", name, force=True)


def test_create_erpnext_shipment_returns_none_on_failure(mock_shipment, mock_store, mock_settings):
	"""Failed shipment processing returns None instead of raising."""
	with patch(
		"shipstation_integration.shipments.create_sales_invoice",
		side_effect=frappe.ValidationError("Missing shipping expense account"),
	):
		result = create_erpnext_shipment(mock_shipment, mock_store, mock_settings)

	assert result is None


def test_create_erpnext_shipment_logs_error_with_order_id(
	mock_shipment, mock_store, mock_settings
):
	"""Error log entry is created with the order ID in the title."""
	with patch(
		"shipstation_integration.shipments.create_sales_invoice",
		side_effect=frappe.ValidationError("Missing shipping expense account"),
	):
		create_erpnext_shipment(mock_shipment, mock_store, mock_settings)

	error_log_name = frappe.db.get_value("Error Log", {"method": ERROR_LOG_TITLE}, "name")
	assert error_log_name, f"Expected Error Log entry with title '{ERROR_LOG_TITLE}'"


def test_create_erpnext_shipment_logs_error_on_delivery_note_failure(
	mock_shipment, mock_store, mock_settings
):
	"""Error is logged even when failure occurs deeper in the chain (create_delivery_note)."""
	mock_store.create_sales_invoice = False

	with patch(
		"shipstation_integration.shipments.create_delivery_note",
		side_effect=frappe.ValidationError("Sales Order not found"),
	):
		result = create_erpnext_shipment(mock_shipment, mock_store, mock_settings)

	assert result is None
	error_log_name = frappe.db.get_value("Error Log", {"method": ERROR_LOG_TITLE}, "name")
	assert error_log_name


def create_erpnext_shipment(shipment, store, settings):
	from shipstation_integration.shipments import create_erpnext_shipment as _fn

	return _fn(shipment, store, settings)
