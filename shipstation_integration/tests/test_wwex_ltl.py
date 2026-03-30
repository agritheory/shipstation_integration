# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import time
from unittest.mock import MagicMock

import frappe
import pytest

from shipstation_integration.shipstation_integration.freight_providers.wwex_ltl import WwexLTL
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
	"""Context manager that returns canned responses in order."""

	def __init__(self, responses):
		self._responses = list(responses)
		self._index = 0

	def __enter__(self):
		return self

	def __exit__(self, *args):
		pass

	def _next(self):
		resp = self._responses[self._index % len(self._responses)]
		self._index += 1
		return resp

	def post(self, *args, **kwargs):
		return self._next()

	def get(self, *args, **kwargs):
		return self._next()

	def delete(self, *args, **kwargs):
		return self._next()


# ---------------------------------------------------------------------------
# Fixture response data
# ---------------------------------------------------------------------------

_SHOP_FLOW_RESPONSE = MockResponse(
	json_data={
		"response": {
			"shipmentProductTransactionId": "txn-wwex-001",
			"shipmentOfferList": [
				{
					"shipmentOfferId": "offer-wwex-001",
					"carrierName": "Estes Express",
					"scac": "EXLA",
					"serviceDescription": "Standard LTL",
					"transitDays": 3,
					"price": {"netPrice": 425.50},
				}
			],
		}
	}
)

_SHOP_FLOW_EMPTY_RESPONSE = MockResponse(
	json_data={"response": {"shipmentProductTransactionId": "txn-wwex-002", "shipmentOfferList": []}}
)

_QUOTE_ORDER_FLOW_RESPONSE = MockResponse(
	json_data={
		"response": {
			"bolNumber": "BOL-WWEX-001",
			"proNumber": "PRO-WWEX-001",
			"pickupTxnId": "pickup-wwex-001",
		}
	}
)

_DOCUMENT_DOWNLOAD_FLOW_RESPONSE = MockResponse(
	json_data={
		"response": {
			"documents": [{"documentType": "BILL_OF_LADING", "content": "JVBERi0=", "fileName": "bol.pdf"}]
		}
	}
)

_SEARCH_SHIPMENTS_RESPONSE = MockResponse(
	json_data={"response": {"status": "IN_TRANSIT", "proNumber": "PRO-WWEX-001"}}
)

_CANCEL_FLOW_RESPONSE = MockResponse(
	json_data={"response": {"confirmationNumber": "CANCEL-WWEX-001"}}
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_wwex_fcs_name():
	return frappe.db.get_value("Supplier", {"supplier_name": "WWEX LTL", "is_transporter": 1}, "name")


def _get_wwex_settings_name():
	supplier = _get_wwex_fcs_name()
	if not supplier:
		return None
	return frappe.db.get_value(
		"Freight Carrier Settings",
		{"company": "Ambrosia Pie Company", "supplier": supplier},
		"name",
	)


def _inject_wwex_token(fc_name):
	"""Inject a valid cached token so OAuth is never called during tests."""
	frappe.cache.set_value(
		f"wwex_token:{fc_name}",
		{"access_token": "test-wwex-bearer-token", "expires_at": time.time() + 7200},
	)


# ---------------------------------------------------------------------------
# get_ltl_quotes
# ---------------------------------------------------------------------------


@pytest.mark.order(100)
def test_wwex_get_ltl_quotes_creates_shipment_quotations(monkeypatch):
	settings_name = _get_wwex_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_wwex_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SHOP_FLOW_RESPONSE]))

	provider = WwexLTL()
	provider.get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["quote_or_offer_id", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_id"] == "offer-wwex-001"
	assert saved[0]["quote_or_offer_transaction_id"] == "txn-wwex-001"


@pytest.mark.order(102)
def test_wwex_get_ltl_quotes_returns_message(monkeypatch):
	settings_name = _get_wwex_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_wwex_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SHOP_FLOW_RESPONSE]))

	provider = WwexLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is not None
	assert "1" in result


@pytest.mark.order(104)
def test_wwex_get_ltl_quotes_no_offers_returns_none(monkeypatch):
	settings_name = _get_wwex_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_wwex_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SHOP_FLOW_EMPTY_RESPONSE]))

	provider = WwexLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is None


# ---------------------------------------------------------------------------
# schedule_ltl_pickup
# ---------------------------------------------------------------------------


def _create_wwex_accepted_quotation(shipment, settings_name):
	"""Insert and submit a Shipment Quotation with WWEX IDs."""
	sq = frappe.new_doc("Shipment Quotation")
	sq.shipment = shipment.name
	sq.carrier = shipment.preferred_carrier or "WWEX LTL"
	sq.carrier_scac = "EXLA"
	sq.quote_or_offer_id = "offer-wwex-001"
	sq.quote_or_offer_transaction_id = "txn-wwex-001"
	sq.service_level = "Standard LTL"
	sq.grand_total = 425.50
	sq.pickup_date = shipment.pickup_date
	sq.insert(ignore_permissions=True)
	sq.submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", sq.name)
	return sq


@pytest.mark.order(106)
def test_wwex_schedule_ltl_pickup_sets_awb_number(monkeypatch):
	settings_name = _get_wwex_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_wwex_token(settings_name)
	_create_wwex_accepted_quotation(shipment, settings_name)

	shared_client = MockHttpxClient([_QUOTE_ORDER_FLOW_RESPONSE, _DOCUMENT_DOWNLOAD_FLOW_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	WwexLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-WWEX-001"


@pytest.mark.order(108)
def test_wwex_schedule_ltl_pickup_sets_shipment_id(monkeypatch):
	settings_name = _get_wwex_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_wwex_token(settings_name)
	_create_wwex_accepted_quotation(shipment, settings_name)

	shared_client = MockHttpxClient([_QUOTE_ORDER_FLOW_RESPONSE, _DOCUMENT_DOWNLOAD_FLOW_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	WwexLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("shipment_id") == "txn-wwex-001"


@pytest.mark.order(110)
def test_wwex_schedule_ltl_pickup_attaches_bol(monkeypatch):
	settings_name = _get_wwex_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_wwex_token(settings_name)
	_create_wwex_accepted_quotation(shipment, settings_name)

	shared_client = MockHttpxClient([_QUOTE_ORDER_FLOW_RESPONSE, _DOCUMENT_DOWNLOAD_FLOW_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	WwexLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Shipment", "attached_to_name": shipment.name},
		fields=["file_name"],
	)
	assert len(attachments) >= 1


# ---------------------------------------------------------------------------
# cancel_shipment
# ---------------------------------------------------------------------------


@pytest.mark.order(112)
def test_wwex_cancel_shipment_returns_confirmation(monkeypatch):
	settings_name = _get_wwex_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "shipment_id", "txn-wwex-001")
	shipment.reload()
	_inject_wwex_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_CANCEL_FLOW_RESPONSE]))

	provider = WwexLTL()
	result = provider.cancel_shipment(shipment, settings_name=settings_name)

	assert result is not None
	assert "CANCEL-WWEX-001" in str(result)

	frappe.db.set_value("Shipment", shipment.name, "shipment_id", None)


# ---------------------------------------------------------------------------
# track_shipment
# ---------------------------------------------------------------------------


@pytest.mark.order(114)
def test_wwex_track_shipment_calls_search_flow(monkeypatch):
	settings_name = _get_wwex_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "awb_number", "PRO-WWEX-001")
	shipment.reload()
	_inject_wwex_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SEARCH_SHIPMENTS_RESPONSE]))

	provider = WwexLTL()
	result = provider.track_shipment(shipment, settings_name=settings_name)

	assert "IN_TRANSIT" in str(result)

	frappe.db.set_value("Shipment", shipment.name, "awb_number", None)


# ---------------------------------------------------------------------------
# get_documents
# ---------------------------------------------------------------------------


@pytest.mark.order(116)
def test_wwex_get_documents_returns_list(monkeypatch):
	settings_name = _get_wwex_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "shipment_id", "txn-wwex-001")
	shipment.reload()
	_inject_wwex_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_DOCUMENT_DOWNLOAD_FLOW_RESPONSE]))

	provider = WwexLTL()
	docs = provider.get_documents(shipment, settings_name=settings_name)

	assert len(docs) == 1
	doc_type = docs[0].get("document_type") or docs[0].get("documentType") or ""
	assert "BILL_OF_LADING" in doc_type or "BOL" in doc_type

	frappe.db.set_value("Shipment", shipment.name, "shipment_id", None)


# ---------------------------------------------------------------------------
# validate_required_shipment_form_fields
# ---------------------------------------------------------------------------


@pytest.mark.order(118)
def test_wwex_validate_missing_pickup_address_returns_message():
	doc = MagicMock()
	doc.get = lambda key, default=None: None
	provider = WwexLTL()
	result = provider.validate_required_shipment_form_fields(doc)
	assert result is not None
	assert len(result) > 0


@pytest.mark.order(120)
def test_wwex_validate_complete_doc_returns_none():
	shipment = get_draft_ltl_shipment_for_tests()
	provider = WwexLTL()
	result = provider.validate_required_shipment_form_fields(shipment)
	assert result is None


# ---------------------------------------------------------------------------
# Static / metadata methods
# ---------------------------------------------------------------------------


@pytest.mark.order(122)
def test_wwex_get_package_type_options_returns_pallet():
	provider = WwexLTL()
	options = provider.get_package_type_options()
	values = [o["value"] for o in options]
	assert "PLT" in values


@pytest.mark.order(124)
def test_wwex_get_accessorial_service_fields_includes_liftgate():
	shipment = get_draft_ltl_shipment_for_tests()
	provider = WwexLTL()
	result = provider.get_accessorial_service_fields(shipment)
	supported = result.get("supported", [])
	# Supported may be field names (strings) or dicts
	names = [s if isinstance(s, str) else s.get("fieldname", "") for s in supported]
	assert "lift_gate_required_at_delivery" in names


@pytest.mark.order(126)
def test_wwex_supports_quote_but_not_spot_quote():
	shipment = get_draft_ltl_shipment_for_tests()
	provider = WwexLTL()
	result = provider.supports_quote_or_spot_quote(shipment)
	assert result["supports_quote"] is True
	assert result["supports_spot_quote"] is False


@pytest.mark.order(130)
def test_wwex_full_story_customer_delivery_shipment(monkeypatch):
	"""
	Complete dispatch lifecycle on the standard customer-delivery LTL shipment:
	quote → accept → schedule pickup → track → get documents → cancel.

	Verifies that the IDs written by each step are the values consumed by the
	next, catching any field-name mismatches between provider methods.
	"""
	settings_name = _get_wwex_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_wwex_token(settings_name)

	# Step 1: quote
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SHOP_FLOW_RESPONSE]))
	WwexLTL().get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	sq_name = saved[0]["name"]
	assert saved[0]["quote_or_offer_id"] == "offer-wwex-001"

	# Step 2: accept — submit the SQ (dispatcher action) and record it
	frappe.get_doc("Shipment Quotation", sq_name).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", sq_name)
	_inject_wwex_token(settings_name)

	# Step 3: schedule pickup
	shared_client = MockHttpxClient([_QUOTE_ORDER_FLOW_RESPONSE, _DOCUMENT_DOWNLOAD_FLOW_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	WwexLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-WWEX-001"
	assert shipment.get("shipment_id") == "txn-wwex-001"

	# Step 4: track — uses awb_number written by schedule
	_inject_wwex_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SEARCH_SHIPMENTS_RESPONSE]))
	track_result = WwexLTL().track_shipment(shipment, settings_name=settings_name)
	assert "IN_TRANSIT" in str(track_result)

	# Step 5: get documents — uses shipment_id written by schedule
	_inject_wwex_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_DOCUMENT_DOWNLOAD_FLOW_RESPONSE]))
	docs = WwexLTL().get_documents(shipment, settings_name=settings_name)
	assert len(docs) >= 1

	# Step 6: cancel — uses shipment_id written by schedule
	_inject_wwex_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_CANCEL_FLOW_RESPONSE]))
	cancel_result = WwexLTL().cancel_shipment(shipment, settings_name=settings_name)
	assert "CANCEL-WWEX-001" in str(cancel_result)

	frappe.db.set_value("Shipment", shipment.name, {"awb_number": None, "shipment_id": None})


@pytest.mark.order(132)
def test_wwex_full_story_freight_terminal_shipment(monkeypatch):
	"""
	Complete dispatch lifecycle on the freight-terminal LTL shipment
	(delivery_to_type="Contact"), which exercises the Contact address
	resolution path through get_address_and_contact_info.
	"""
	settings_name = _get_wwex_settings_name()

	shipment = get_freight_terminal_shipment_for_tests()

	# Clear any leftover quotations from a prior run
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

	_inject_wwex_token(settings_name)

	# Step 1: quote
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SHOP_FLOW_RESPONSE]))
	WwexLTL().get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id"],
	)
	assert len(saved) == 1
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])
	_inject_wwex_token(settings_name)

	# Step 2: schedule pickup
	shared_client = MockHttpxClient([_QUOTE_ORDER_FLOW_RESPONSE, _DOCUMENT_DOWNLOAD_FLOW_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	WwexLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-WWEX-001"

	# Step 3: cancel
	_inject_wwex_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_CANCEL_FLOW_RESPONSE]))
	cancel_result = WwexLTL().cancel_shipment(shipment, settings_name=settings_name)
	assert "CANCEL-WWEX-001" in str(cancel_result)

	frappe.db.set_value(
		"Shipment",
		shipment.name,
		{"awb_number": None, "shipment_id": None, "accepted_quotation": None},
	)
