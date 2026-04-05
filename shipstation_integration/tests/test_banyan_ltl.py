# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock

import frappe
import pytest

from shipstation_integration.shipstation_integration.freight_providers.banyan_ltl import BanyanLTL
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	get_freight_terminal_shipment_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


# ---------------------------------------------------------------------------
# Shared mock infrastructure
# ---------------------------------------------------------------------------


class MockResponse:
	def __init__(self, json_data=None, text="", status_code=200):
		self._json = json_data
		self.text = text
		self.status_code = status_code

	def json(self):
		return self._json

	def raise_for_status(self):
		pass


class MockHttpxClient:
	def __init__(self, responses):
		self._responses = list(responses)
		self._index = 0

	def __enter__(self):
		return self

	def __exit__(self, *args):
		pass

	def next_response(self):
		resp = self._responses[self._index % len(self._responses)]
		self._index += 1
		return resp

	def post(self, *args, **kwargs):
		return self.next_response()

	def get(self, *args, **kwargs):
		return self.next_response()

	def delete(self, *args, **kwargs):
		return self.next_response()


# ---------------------------------------------------------------------------
# Fixture response data
# ---------------------------------------------------------------------------

SHIPMENTS_RESPONSE = MockResponse(
	json_data={
		"loadId": "load-banyan-001",
		"quotes": [
			{
				"quoteId": 42,
				"carrierName": "Saia LTL",
				"scac": "SAIA",
				"serviceDescription": "Standard",
				"transitDays": 2,
				"rawPrice": {"netPrice": 310.00},
			}
		],
	}
)

SHIPMENTS_NO_QUOTES_RESPONSE = MockResponse(json_data={"loadId": "load-banyan-002", "quotes": []})

BOOK_RESPONSE = MockResponse(json_data={"proNumber": "PRO-BAN-001", "bolNumber": "BOL-BAN-001"})

DOCUMENTS_RESPONSE = MockResponse(
	json_data=[{"documentType": "BOL", "content": "JVBERi0=", "fileName": "bol_banyan.pdf"}]
)

TRACKING_RESPONSE = MockResponse(json_data={"status": "DELIVERED", "loadId": "load-banyan-001"})

CANCEL_RESPONSE = MockResponse(json_data={"confirmationNumber": "CANCEL-BAN-001"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_banyan_settings_name():
	supplier = frappe.db.get_value(
		"Supplier", {"supplier_name": "Banyan LTL", "is_transporter": 1}, "name"
	)
	if not supplier:
		return None
	return frappe.db.get_value(
		"Freight Carrier Settings",
		{"company": "Ambrosia Pie Company", "supplier": supplier},
		"name",
	)


def create_banyan_accepted_quotation(shipment, settings_name):
	sq = frappe.new_doc("Shipment Quotation")
	sq.shipment = shipment.name
	sq.carrier = shipment.preferred_carrier or "Banyan LTL"
	sq.carrier_scac = "SAIA"
	sq.quote_or_offer_id = "42"
	sq.quote_or_offer_transaction_id = "load-banyan-001"
	sq.service_level = "Standard"
	sq.grand_total = 310.00
	sq.pickup_date = shipment.pickup_date
	sq.insert(ignore_permissions=True)
	sq.submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", sq.name)
	return sq


# ---------------------------------------------------------------------------
# get_ltl_quotes
# ---------------------------------------------------------------------------


@pytest.mark.order(200)
def test_banyan_get_ltl_quotes_creates_shipment_quotations(monkeypatch):
	settings_name = get_banyan_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_RESPONSE]))

	provider = BanyanLTL()
	provider.get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["quote_or_offer_id", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	assert str(saved[0]["quote_or_offer_id"]) == "42"
	assert saved[0]["quote_or_offer_transaction_id"] == "load-banyan-001"


@pytest.mark.order(202)
def test_banyan_get_ltl_quotes_returns_message(monkeypatch):
	settings_name = get_banyan_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_RESPONSE]))

	provider = BanyanLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is not None
	assert "1" in result


@pytest.mark.order(204)
def test_banyan_get_ltl_quotes_no_quotes_returns_none(monkeypatch):
	settings_name = get_banyan_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_NO_QUOTES_RESPONSE]))

	provider = BanyanLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is None


# ---------------------------------------------------------------------------
# schedule_ltl_pickup
# ---------------------------------------------------------------------------


@pytest.mark.order(206)
def test_banyan_schedule_ltl_pickup_sets_awb_number(monkeypatch):
	settings_name = get_banyan_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	create_banyan_accepted_quotation(shipment, settings_name)

	shared_client = MockHttpxClient([BOOK_RESPONSE, DOCUMENTS_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-BAN-001"


@pytest.mark.order(208)
def test_banyan_schedule_ltl_pickup_sets_shipment_id(monkeypatch):
	settings_name = get_banyan_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	create_banyan_accepted_quotation(shipment, settings_name)

	shared_client = MockHttpxClient([BOOK_RESPONSE, DOCUMENTS_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("shipment_id") == "load-banyan-001"


@pytest.mark.order(210)
def test_banyan_schedule_ltl_pickup_attaches_bol(monkeypatch):
	settings_name = get_banyan_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	create_banyan_accepted_quotation(shipment, settings_name)

	shared_client = MockHttpxClient([BOOK_RESPONSE, DOCUMENTS_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Shipment", "attached_to_name": shipment.name},
		fields=["file_name"],
	)
	assert len(attachments) >= 1


# ---------------------------------------------------------------------------
# cancel_shipment
# ---------------------------------------------------------------------------


@pytest.mark.order(212)
def test_banyan_cancel_shipment_returns_confirmation(monkeypatch):
	settings_name = get_banyan_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "shipment_id", "load-banyan-001")
	shipment.reload()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([CANCEL_RESPONSE]))

	provider = BanyanLTL()
	result = provider.cancel_shipment(shipment, settings_name=settings_name)

	assert result is not None
	assert "CANCEL-BAN-001" in str(result)

	frappe.db.set_value("Shipment", shipment.name, "shipment_id", None)


# ---------------------------------------------------------------------------
# track_shipment
# ---------------------------------------------------------------------------


@pytest.mark.order(214)
def test_banyan_track_shipment_returns_status(monkeypatch):
	settings_name = get_banyan_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "shipment_id", "load-banyan-001")
	shipment.reload()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([TRACKING_RESPONSE]))

	provider = BanyanLTL()
	result = provider.track_shipment(shipment, settings_name=settings_name)

	assert "DELIVERED" in str(result)

	frappe.db.set_value("Shipment", shipment.name, "shipment_id", None)


# ---------------------------------------------------------------------------
# get_documents
# ---------------------------------------------------------------------------


@pytest.mark.order(216)
def test_banyan_get_documents_returns_list(monkeypatch):
	settings_name = get_banyan_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "shipment_id", "load-banyan-001")
	shipment.reload()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([DOCUMENTS_RESPONSE]))

	provider = BanyanLTL()
	docs = provider.get_documents(shipment, settings_name=settings_name)

	assert len(docs) == 1
	doc_type = docs[0].get("document_type") or docs[0].get("documentType") or ""
	assert "BOL" in doc_type

	frappe.db.set_value("Shipment", shipment.name, "shipment_id", None)


# ---------------------------------------------------------------------------
# validate_required_shipment_form_fields
# ---------------------------------------------------------------------------


@pytest.mark.order(218)
def test_banyan_validate_missing_delivery_address_returns_message():
	doc = MagicMock()
	doc.get = lambda key, default=None: (
		"some-pickup-address" if key == "pickup_address_name" else None
	)
	provider = BanyanLTL()
	result = provider.validate_required_shipment_form_fields(doc)
	assert result is not None
	assert len(result) > 0


@pytest.mark.order(220)
def test_banyan_validate_complete_doc_returns_none():
	shipment = get_draft_ltl_shipment_for_tests()
	provider = BanyanLTL()
	result = provider.validate_required_shipment_form_fields(shipment)
	assert result is None


# ---------------------------------------------------------------------------
# Static / metadata methods
# ---------------------------------------------------------------------------


@pytest.mark.order(222)
def test_banyan_get_package_type_options_returns_pallets():
	provider = BanyanLTL()
	options = provider.get_package_type_options()
	labels = [o.get("label", "") for o in options]
	values = [o.get("value", "") for o in options]
	assert any("Pallet" in l for l in labels) or any("PLT" in v for v in values)


@pytest.mark.order(224)
def test_banyan_get_accessorial_service_fields_includes_liftgate():
	shipment = get_draft_ltl_shipment_for_tests()
	provider = BanyanLTL()
	result = provider.get_accessorial_service_fields(shipment)
	supported = result.get("supported", [])
	names = [s if isinstance(s, str) else s.get("fieldname", "") for s in supported]
	assert "lift_gate_required_at_delivery" in names


@pytest.mark.order(230)
def test_banyan_full_story_customer_delivery_shipment(monkeypatch):
	"""
	Complete dispatch lifecycle on the standard customer-delivery LTL shipment:
	quote → accept → schedule pickup → track → get documents → cancel.

	Verifies that the loadId written by get_ltl_quotes is the same value
	consumed as shipment_id by schedule_ltl_pickup and cancel_shipment.
	"""
	settings_name = get_banyan_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	# Step 1: quote
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_RESPONSE]))
	BanyanLTL().get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_transaction_id"] == "load-banyan-001"

	# Step 2: accept — submit the SQ (dispatcher action) and record it
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])

	# Step 3: schedule pickup
	shared_client = MockHttpxClient([BOOK_RESPONSE, DOCUMENTS_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-BAN-001"
	assert shipment.get("shipment_id") == "load-banyan-001"

	# Step 4: track — uses shipment_id written by schedule
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([TRACKING_RESPONSE]))
	track_result = BanyanLTL().track_shipment(shipment, settings_name=settings_name)
	assert "DELIVERED" in str(track_result)

	# Step 5: get documents — uses shipment_id written by schedule
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([DOCUMENTS_RESPONSE]))
	docs = BanyanLTL().get_documents(shipment, settings_name=settings_name)
	assert len(docs) >= 1

	# Step 6: cancel — uses shipment_id written by schedule
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([CANCEL_RESPONSE]))
	cancel_result = BanyanLTL().cancel_shipment(shipment, settings_name=settings_name)
	assert "CANCEL-BAN-001" in str(cancel_result)

	frappe.db.set_value("Shipment", shipment.name, {"awb_number": None, "shipment_id": None})


@pytest.mark.order(232)
def test_banyan_full_story_freight_terminal_shipment(monkeypatch):
	"""
	Complete dispatch lifecycle on the freight-terminal LTL shipment
	(delivery_to_type="Contact"), which exercises the Contact address
	resolution path through get_address_and_contact_info.
	"""
	settings_name = get_banyan_settings_name()

	shipment = get_freight_terminal_shipment_for_tests()

	for sq in frappe.get_all("Shipment Quotation", filters={"shipment": shipment.name}, pluck="name"):
		sq_doc = frappe.get_doc("Shipment Quotation", sq)
		if sq_doc.docstatus == 1:
			sq_doc.flags.ignore_permissions = True
			sq_doc.cancel()
		frappe.delete_doc("Shipment Quotation", sq, force=True)
	frappe.db.set_value(
		"Shipment",
		shipment.name,
		{"awb_number": None, "shipment_id": None, "accepted_quotation": None},
	)
	shipment.reload()

	# Step 1: quote
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_RESPONSE]))
	BanyanLTL().get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])

	# Step 2: schedule pickup
	shared_client = MockHttpxClient([BOOK_RESPONSE, DOCUMENTS_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-BAN-001"

	# Step 3: cancel
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([CANCEL_RESPONSE]))
	cancel_result = BanyanLTL().cancel_shipment(shipment, settings_name=settings_name)
	assert "CANCEL-BAN-001" in str(cancel_result)

	frappe.db.set_value(
		"Shipment",
		shipment.name,
		{"awb_number": None, "shipment_id": None, "accepted_quotation": None},
	)
