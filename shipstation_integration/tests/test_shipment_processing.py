# Copyright (c) 2026, AgriTheory and Contributors
# See license.txt

from unittest.mock import MagicMock

import frappe
import pytest
from shipstation.models import ShipStationOrder

from shipstation_integration.shipments import create_erpnext_shipment


ORDER_ID = "test-shipstation-order-99999"
ERROR_LOG_TITLE = f"Shipstation: order {ORDER_ID}"


def make_mock_shipment():
	shipment = MagicMock(spec=ShipStationOrder)
	shipment.order_id = ORDER_ID
	shipment.shipment_id = "test-shipment-99999"
	return shipment


def make_mock_store(create_sales_invoice=True, create_delivery_note=True):
	store = MagicMock()
	store.create_sales_invoice = create_sales_invoice
	store.create_delivery_note = create_delivery_note
	store.create_shipment = False
	return store


def make_mock_settings():
	settings = MagicMock()
	settings.shipstation_user = None
	return settings


def cleanup_error_log():
	for name in frappe.get_all("Error Log", filters={"method": ERROR_LOG_TITLE}, pluck="name"):
		frappe.delete_doc("Error Log", name, force=True)


def test_create_erpnext_shipment_returns_none_on_failure(monkeypatch):
	monkeypatch.setattr(
		"shipstation_integration.shipments.create_sales_invoice",
		MagicMock(side_effect=frappe.ValidationError("Missing shipping expense account")),
	)
	result = create_erpnext_shipment(make_mock_shipment(), make_mock_store(), make_mock_settings())
	assert result is None
	cleanup_error_log()


def test_create_erpnext_shipment_logs_error_with_order_id(monkeypatch):
	monkeypatch.setattr(
		"shipstation_integration.shipments.create_sales_invoice",
		MagicMock(side_effect=frappe.ValidationError("Missing shipping expense account")),
	)
	create_erpnext_shipment(make_mock_shipment(), make_mock_store(), make_mock_settings())

	error_log_name = frappe.db.get_value("Error Log", {"method": ERROR_LOG_TITLE}, "name")
	assert error_log_name, f"Expected Error Log entry with title '{ERROR_LOG_TITLE}'"
	cleanup_error_log()


def test_create_erpnext_shipment_logs_error_on_delivery_note_failure(monkeypatch):
	monkeypatch.setattr(
		"shipstation_integration.shipments.create_delivery_note",
		MagicMock(side_effect=frappe.ValidationError("Sales Order not found")),
	)
	result = create_erpnext_shipment(
		make_mock_shipment(),
		make_mock_store(create_sales_invoice=False),
		make_mock_settings(),
	)

	assert result is None
	error_log_name = frappe.db.get_value("Error Log", {"method": ERROR_LOG_TITLE}, "name")
	assert error_log_name
	cleanup_error_log()
