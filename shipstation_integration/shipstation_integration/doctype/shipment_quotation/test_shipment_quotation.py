# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from unittest.mock import patch

import frappe
import pytest

from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	ltl_pickup_response_for_tests,
	ltl_quotes_response_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


def test_get_ltl_quotes_creates_quotation_docs():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	quotes_fixture = ltl_quotes_response_for_tests()
	ltl = ShipstationLTL()

	msg = ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, quotes_fixture, is_spot_quote=False
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


def test_get_ltl_quotes_with_spot_quotes():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	quotes_fixture = ltl_quotes_response_for_tests()

	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, quotes_fixture[:1], is_spot_quote=True
	)

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	assert sq.is_spot_quote == 1


def test_accept_quote_sets_shipment_fields():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	quotes_fixture = ltl_quotes_response_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(ltl_shipment, quotes_fixture[:1])

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.accept_quote = 1
	sq.save()

	ltl_shipment.reload()
	assert ltl_shipment.accepted_quotation == sq.name
	assert ltl_shipment.get("quote_or_offer_id") == sq.quote_or_offer_id


def test_only_one_accepted_quote_allowed():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	quotes_fixture = ltl_quotes_response_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(ltl_shipment, quotes_fixture)

	all_sqs = frappe.get_all(
		"Shipment Quotation", filters={"shipment": ltl_shipment.name}, pluck="name"
	)
	assert len(all_sqs) >= 2

	first = frappe.get_doc("Shipment Quotation", all_sqs[0])
	first.accept_quote = 1
	first.save()

	second = frappe.get_doc("Shipment Quotation", all_sqs[1])
	second.accept_quote = 1
	with pytest.raises(frappe.ValidationError):
		second.save()


def test_unaccepting_quote_clears_shipment_fields():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	quotes_fixture = ltl_quotes_response_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(ltl_shipment, quotes_fixture[:1])

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.accept_quote = 1
	sq.save()

	ltl_shipment.reload()
	assert ltl_shipment.accepted_quotation == sq.name

	sq.reload()
	sq.accept_quote = 0
	sq.save()

	ltl_shipment.reload()
	assert not ltl_shipment.accepted_quotation
	assert not ltl_shipment.get("quote_or_offer_id")


def test_schedule_pickup_attaches_bol():
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	quotes_fixture = ltl_quotes_response_for_tests()
	pickup_fixture = ltl_pickup_response_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(ltl_shipment, quotes_fixture[:1])

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.accept_quote = 1
	sq.save()

	ltl_shipment.reload()
	ltl_shipment.accepted_quotation = sq.name
	ltl_shipment.save()

	with patch.object(ltl, "schedule_ltl_pickup_with_quote_id", return_value=pickup_fixture):
		with patch.object(ltl, "supports_scheduled_pickup", return_value={"supports_pickup": True}):
			with patch.object(ltl, "validate_carrier_and_id"):
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


def test_check_if_shipment_pickup_scheduled_returns_false_when_not_scheduled():
	from shipstation_integration.shipstation_integration.doctype.shipment_quotation.shipment_quotation import (
		check_if_shipment_pickup_scheduled,
	)

	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()

	mock_doc = frappe._dict({"shipment": ltl_shipment.name})
	result = check_if_shipment_pickup_scheduled(mock_doc)
	assert result["pickup_scheduled"] is False


def test_check_if_shipment_pickup_scheduled_returns_true_when_scheduled():
	from shipstation_integration.shipstation_integration.doctype.shipment_quotation.shipment_quotation import (
		check_if_shipment_pickup_scheduled,
	)

	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", ltl_shipment.name, "pickup_id", "test-pickup-id")
	mock_doc = frappe._dict({"shipment": ltl_shipment.name})
	result = check_if_shipment_pickup_scheduled(mock_doc)
	assert result["pickup_scheduled"] is True

	frappe.db.set_value("Shipment", ltl_shipment.name, "pickup_id", None)
