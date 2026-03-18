# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Tests for the ShipmentQuotation doctype and LTL quote/pickup workflow.

Each test section corresponds to a user story:
  1. Getting LTL quotes creates ShipmentQuotation docs with correct charges.
  2. Accepting a quote sets the linked Shipment's quotation fields.
  3. Un-accepting a quote clears those Shipment fields.
  4. Scheduling a pickup (mocked API) writes fields and attaches the BOL.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
import pytest

from shipstation_integration.ltl import ShipstationLTL


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict:
	fixtures_dir = Path(frappe.get_app_path("shipstation_integration", "tests", "fixtures"))
	return json.loads((fixtures_dir / name).read_text())


@pytest.fixture
def quotes_fixture():
	return _load_fixture("ltl_quotes_response.json")["captured_responses"][0]["response"]


@pytest.fixture
def pickup_fixture():
	return _load_fixture("ltl_pickup_response.json")["captured_responses"][0]["response"]


@pytest.fixture
def ltl_shipment(db_instance):
	"""Return the draft LTL Shipment created by setup.create_shipment_for_ltl."""
	shipment = frappe.get_last_doc("Shipment", filters={"freight_type": "LTL", "docstatus": 0})
	shipment.reload()

	# Remember fields we might modify so we can restore them afterwards.
	original = {
		"accepted_quotation": shipment.get("accepted_quotation"),
		"quote_or_offer_id": shipment.get("quote_or_offer_id"),
		"quote_or_offer_transaction_id": shipment.get("quote_or_offer_transaction_id"),
		"estimated_delivery_date": shipment.get("estimated_delivery_date"),
		"pickup_id": shipment.get("pickup_id"),
		"awb_number": shipment.get("awb_number"),
		"shipment_id": shipment.get("shipment_id"),
	}

	yield shipment

	# Restore
	shipment.reload()
	for field, val in original.items():
		frappe.db.set_value("Shipment", shipment.name, field, val)

	# Delete any ShipmentQuotation docs created during the test
	for sq in frappe.get_all("Shipment Quotation", filters={"shipment": shipment.name}, pluck="name"):
		frappe.delete_doc("Shipment Quotation", sq, force=True)


# ---------------------------------------------------------------------------
# 1. get_ltl_quotes creates ShipmentQuotation docs
# ---------------------------------------------------------------------------


def test_get_ltl_quotes_creates_quotation_docs(db_instance, ltl_shipment, quotes_fixture):
	"""Calling save_ltl_quotes_as_shipment_quotations_and_display with two fixture quotes
	should create two ShipmentQuotation docs with correct charges and totals.
	"""
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

	# First quote: Standard LTL, total 474.38, 3 days
	std = next((q for q in saved if q.grand_total == pytest.approx(474.38, abs=0.01)), None)
	assert std is not None, "Standard LTL quote not found"
	assert std.service_level == "Standard LTL"
	assert std.estimated_delivery_days == 3

	# Second quote: Expedited LTL, total 647.35, 2 days
	exp = next((q for q in saved if q.grand_total == pytest.approx(647.35, abs=0.01)), None)
	assert exp is not None, "Expedited LTL quote not found"
	assert exp.service_level == "Expedited LTL"
	assert exp.estimated_delivery_days == 2

	# Charges table: discount should be stored as negative
	exp_doc = frappe.get_doc("Shipment Quotation", exp.name)
	discount_rows = [c for c in exp_doc.charges if c.type == "Discount"]
	assert len(discount_rows) == 1
	assert discount_rows[0].amount < 0, "Discount charge should be stored as negative"

	# Message string should mention both quotes
	assert "Standard LTL" in msg
	assert "Expedited LTL" in msg


def test_get_ltl_quotes_with_spot_quotes(db_instance, ltl_shipment, quotes_fixture):
	"""is_spot_quote=True should persist the flag on each ShipmentQuotation doc."""
	# Delete existing quotations first
	for sq in frappe.get_all(
		"Shipment Quotation", filters={"shipment": ltl_shipment.name}, pluck="name"
	):
		frappe.delete_doc("Shipment Quotation", sq, force=True)

	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, quotes_fixture[:1], is_spot_quote=True
	)

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	assert sq.is_spot_quote == 1


# ---------------------------------------------------------------------------
# 2. Accepting a quote sets Shipment fields
# ---------------------------------------------------------------------------


def test_accept_quote_sets_shipment_fields(db_instance, ltl_shipment, quotes_fixture):
	"""Setting accept_quote=1 on a ShipmentQuotation should propagate to the Shipment."""
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(ltl_shipment, quotes_fixture[:1])

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.accept_quote = 1
	sq.save()

	ltl_shipment.reload()
	assert ltl_shipment.accepted_quotation == sq.name
	assert ltl_shipment.get("quote_or_offer_id") == sq.quote_or_offer_id


def test_only_one_accepted_quote_allowed(db_instance, ltl_shipment, quotes_fixture):
	"""Trying to accept a second quote when one is already accepted should throw."""
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


# ---------------------------------------------------------------------------
# 3. Un-accepting a quote clears Shipment fields
# ---------------------------------------------------------------------------


def test_unaccepting_quote_clears_shipment_fields(db_instance, ltl_shipment, quotes_fixture):
	"""Un-checking accept_quote should clear the accepted_quotation and related fields on Shipment."""
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(ltl_shipment, quotes_fixture[:1])

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.accept_quote = 1
	sq.save()

	ltl_shipment.reload()
	assert ltl_shipment.accepted_quotation == sq.name

	# Now un-accept
	sq.reload()
	sq.accept_quote = 0
	sq.save()

	ltl_shipment.reload()
	assert not ltl_shipment.accepted_quotation
	assert not ltl_shipment.get("quote_or_offer_id")


# ---------------------------------------------------------------------------
# 4. Scheduling a pickup attaches the BOL
# ---------------------------------------------------------------------------


def test_schedule_pickup_attaches_bol(db_instance, ltl_shipment, quotes_fixture, pickup_fixture):
	"""schedule_ltl_pickup should write Shipment fields and create a file attachment."""
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(ltl_shipment, quotes_fixture[:1])

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.accept_quote = 1
	sq.save()

	ltl_shipment.reload()
	ltl_shipment.accepted_quotation = sq.name
	ltl_shipment.save()

	# Mock only the low-level HTTP call; let the rest of schedule_ltl_pickup run.
	with patch.object(ltl, "schedule_ltl_pickup_with_quote_id", return_value=pickup_fixture):
		with patch.object(ltl, "supports_scheduled_pickup", return_value={"supports_pickup": True}):
			with patch.object(ltl, "validate_carrier_and_id"):
				msg = ltl.schedule_ltl_pickup(ltl_shipment)

	ltl_shipment.reload()
	assert ltl_shipment.get("pickup_id") == pickup_fixture["pickup_id"]
	assert ltl_shipment.get("awb_number") == pickup_fixture["pro_number"]
	assert ltl_shipment.get("shipment_id") == pickup_fixture["shipment_id"]

	# Verify the BOL file was attached
	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Shipment", "attached_to_name": ltl_shipment.name},
		fields=["name", "file_name"],
	)
	assert len(attachments) >= 1, "Expected at least one attached BOL file"
	assert any(
		"bol" in (a.file_name or "").lower() for a in attachments
	), "Expected an attachment with 'bol' in the filename"

	# Confirm success message content
	assert pickup_fixture["pickup_id"] in msg
	assert pickup_fixture["pro_number"] in msg


# ---------------------------------------------------------------------------
# 5. check_if_shipment_pickup_scheduled helper
# ---------------------------------------------------------------------------


def test_check_if_shipment_pickup_scheduled_returns_false_when_not_scheduled(
	db_instance, ltl_shipment
):
	from shipstation_integration.shipstation_integration.doctype.shipment_quotation.shipment_quotation import (
		check_if_shipment_pickup_scheduled,
	)

	ltl_shipment.reload()
	frappe.db.set_value("Shipment", ltl_shipment.name, {"pickup_id": None, "awb_number": None})

	mock_doc = frappe._dict({"shipment": ltl_shipment.name})
	result = check_if_shipment_pickup_scheduled(mock_doc)
	assert result["pickup_scheduled"] is False


def test_check_if_shipment_pickup_scheduled_returns_true_when_scheduled(db_instance, ltl_shipment):
	from shipstation_integration.shipstation_integration.doctype.shipment_quotation.shipment_quotation import (
		check_if_shipment_pickup_scheduled,
	)

	frappe.db.set_value("Shipment", ltl_shipment.name, "pickup_id", "test-pickup-id")
	mock_doc = frappe._dict({"shipment": ltl_shipment.name})
	result = check_if_shipment_pickup_scheduled(mock_doc)
	assert result["pickup_scheduled"] is True

	# Clean up
	frappe.db.set_value("Shipment", ltl_shipment.name, "pickup_id", None)
