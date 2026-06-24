# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	ltl_pickup_response_for_tests,
	ltl_quotes_response_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


@pytest.mark.order(10)
def test_get_ltl_quotes_creates_quotation_docs():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	ltl = ShipstationLTL()

	msg = ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, ltl_quotes_response_for_tests(), is_spot_quote=False
	)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": ltl_shipment.name},
		fields=["name", "grand_total", "service_level", "estimated_delivery_days"],
	)
	assert len(saved) == 2, f"Expected 2 quotations, got {len(saved)}"

	std = next((q for q in saved if q.grand_total == pytest.approx(474.38, abs=0.01)), None)
	assert std is not None, "Standard LTL quote not found"
	assert std.service_level == "Standard LTL"
	assert std.estimated_delivery_days == 3

	exp = next((q for q in saved if q.grand_total == pytest.approx(647.35, abs=0.01)), None)
	assert exp is not None, "Expedited LTL quote not found"
	assert exp.service_level == "Expedited LTL"
	assert exp.estimated_delivery_days == 2

	exp_doc = frappe.get_doc("Shipment Quotation", exp.name)
	discount_rows = [c for c in exp_doc.charges if c.type == "Discount"]
	assert len(discount_rows) == 1
	assert discount_rows[0].amount < 0, "Discount charge should be stored as negative"

	assert "Standard LTL" in msg
	assert "Expedited LTL" in msg


@pytest.mark.order(11)
def test_submit_quote_sets_shipment_fields():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, ltl_quotes_response_for_tests()[:1]
	)

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.submit()

	ltl_shipment.reload()
	assert ltl_shipment.accepted_quotation == sq.name
	assert ltl_shipment.get("quote_or_offer_id") == sq.quote_or_offer_id
	assert ltl_shipment.get("shipment_amount") == sq.grand_total


@pytest.mark.order(12)
def test_cancel_quote_clears_shipment_fields():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, ltl_quotes_response_for_tests()[:1]
	)

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.submit()

	ltl_shipment.reload()
	assert ltl_shipment.accepted_quotation == sq.name

	sq.reload()
	sq.cancel()

	ltl_shipment.reload()
	assert not ltl_shipment.accepted_quotation
	assert not ltl_shipment.get("quote_or_offer_id")
	assert not ltl_shipment.get("shipment_amount")


@pytest.mark.order(13)
def test_schedule_pickup_attaches_bol(monkeypatch):
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	pickup_fixture = ltl_pickup_response_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, ltl_quotes_response_for_tests()[:1]
	)

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.submit()

	ltl_shipment.reload()

	monkeypatch.setattr(ltl, "schedule_ltl_pickup_with_quote_id", lambda *a, **kw: pickup_fixture)
	monkeypatch.setattr(ltl, "supports_scheduled_pickup", lambda *a, **kw: {"supports_pickup": True})
	monkeypatch.setattr(ltl, "validate_carrier_and_id", lambda *a, **kw: None)
	msg = ltl.schedule_ltl_pickup(ltl_shipment)

	ltl_shipment.reload()
	assert ltl_shipment.get("pickup_id") == pickup_fixture["pickup_id"]
	assert ltl_shipment.get("awb_number") == pickup_fixture["pro_number"]
	assert ltl_shipment.get("shipment_id") == pickup_fixture["shipment_id"]

	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Shipment", "attached_to_name": ltl_shipment.name},
		fields=["name", "file_name"],
	)
	assert len(attachments) >= 1, "Expected at least one attached BOL file"
	assert any(
		"bill_of_lading" in (a.file_name or "").lower() for a in attachments
	), "Expected an attachment with 'bill_of_lading' in the filename"

	assert pickup_fixture["pickup_id"] in msg
	assert pickup_fixture["pro_number"] in msg
