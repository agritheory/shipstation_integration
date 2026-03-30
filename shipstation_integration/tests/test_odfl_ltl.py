# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import time
from unittest.mock import MagicMock

import frappe
import pytest

from shipstation_integration.shipstation_integration.freight_providers.odfl_ltl import OdflLTL
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

_TOKEN_RESPONSE = MockResponse(
	json_data={"access_token": "odfl-bearer-token-001", "expires_in": 3600}
)

_SOAP_RATE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:rate="http://www.odfl.com/ws/router/types/v4">
  <soapenv:Body>
    <rate:rateResponse>
      <rate:referenceNumber>REF-ODFL-001</rate:referenceNumber>
      <rate:netFreightCharge>480.00</rate:netFreightCharge>
      <rate:grossFreightCharge>600.00</rate:grossFreightCharge>
      <rate:fuelSurchargeCharge>72.00</rate:fuelSurchargeCharge>
      <rate:totalCharge>552.00</rate:totalCharge>
      <rate:transitDays>2</rate:transitDays>
    </rate:rateResponse>
  </soapenv:Body>
</soapenv:Envelope>"""

_SOAP_RATE_NO_TOTAL_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:rate="http://www.odfl.com/ws/router/types/v4">
  <soapenv:Body>
    <rate:rateResponse>
      <rate:referenceNumber>REF-ODFL-002</rate:referenceNumber>
    </rate:rateResponse>
  </soapenv:Body>
</soapenv:Envelope>"""

_SOAP_RATE_RESPONSE = MockResponse(text=_SOAP_RATE_XML)
_SOAP_RATE_NO_TOTAL_RESPONSE = MockResponse(text=_SOAP_RATE_NO_TOTAL_XML)

_EBOL_RESPONSE = MockResponse(
	json_data={
		"proNumber": "PRO-ODFL-001",
		"bolNumber": "BOL-ODFL-001",
		"images": {"bol": "JVBERi0="},
	}
)

_PICKUP_RESPONSE = MockResponse(json_data={"pickupConfirmationNumber": "PICKUP-ODFL-001"})

_TRACKING_RESPONSE = MockResponse(json_data={"proNumber": "PRO-ODFL-001", "status": "IN_TRANSIT"})

_DOCUMENTS_RESPONSE = MockResponse(
	json_data=[{"documentType": "BOL", "content": "JVBERi0=", "fileName": "odfl_bol.pdf"}]
)

_DELETE_OK_RESPONSE = MockResponse(json_data={"cancelled": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_odfl_settings_name():
	supplier = frappe.db.get_value(
		"Supplier", {"supplier_name": "ODFL LTL", "is_transporter": 1}, "name"
	)
	if not supplier:
		return None
	return frappe.db.get_value(
		"Freight Carrier Settings",
		{"company": "Ambrosia Pie Company", "supplier": supplier},
		"name",
	)


def _inject_odfl_token(fc_name):
	"""Pre-populate the session token cache so the auth GET is bypassed."""
	frappe.cache.set_value(
		f"odfl_token:{fc_name}",
		{"access_token": "odfl-bearer-token-001", "expires_at": time.time() + 7200},
	)


def _create_odfl_accepted_quotation(shipment):
	sq = frappe.new_doc("Shipment Quotation")
	sq.shipment = shipment.name
	sq.carrier = shipment.preferred_carrier or "Old Dominion"
	sq.carrier_scac = "ODFL"
	sq.quote_or_offer_id = "REF-ODFL-001"
	sq.quote_or_offer_transaction_id = "REF-ODFL-001"
	sq.service_level = "LTL"
	sq.grand_total = 552.00
	sq.pickup_date = shipment.pickup_date
	sq.insert(ignore_permissions=True)
	sq.submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", sq.name)
	return sq


# ---------------------------------------------------------------------------
# Static helpers — no DB, no HTTP
# ---------------------------------------------------------------------------


@pytest.mark.order(300)
def test_odfl_iso3_country_converts_us():
	assert OdflLTL._iso3_country("US") == "USA"


@pytest.mark.order(302)
def test_odfl_iso3_country_converts_ca():
	assert OdflLTL._iso3_country("CA") == "CAN"


@pytest.mark.order(304)
def test_odfl_iso3_country_unknown_defaults_to_usa():
	assert OdflLTL._iso3_country("XX") == "USA"


@pytest.mark.order(306)
def test_odfl_parse_rate_response_extracts_fields():
	result = OdflLTL._parse_rate_response(_SOAP_RATE_XML)
	assert result.get("referenceNumber") == "REF-ODFL-001"
	assert result.get("totalCharge") == "552.00"
	assert result.get("transitDays") == "2"


@pytest.mark.order(308)
def test_odfl_parse_rate_response_returns_empty_on_bad_xml():
	result = OdflLTL._parse_rate_response("this is not xml <<<")
	assert result == {}


# ---------------------------------------------------------------------------
# get_ltl_quotes
# ---------------------------------------------------------------------------


@pytest.mark.order(310)
def test_odfl_get_ltl_quotes_creates_shipment_quotation(monkeypatch):
	settings_name = _get_odfl_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_odfl_token(settings_name)

	# Token already cached; only the SOAP POST is needed
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SOAP_RATE_RESPONSE]))

	provider = OdflLTL()
	provider.get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["carrier_scac", "grand_total", "quote_or_offer_id"],
	)
	assert len(saved) == 1
	assert saved[0]["carrier_scac"] == "ODFL"
	assert abs(float(saved[0]["grand_total"]) - 552.00) < 0.01
	assert saved[0]["quote_or_offer_id"] == "REF-ODFL-001"


@pytest.mark.order(312)
def test_odfl_get_ltl_quotes_returns_message(monkeypatch):
	settings_name = _get_odfl_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_odfl_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SOAP_RATE_RESPONSE]))

	provider = OdflLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is not None
	assert "552" in result


@pytest.mark.order(314)
def test_odfl_get_ltl_quotes_no_total_returns_none(monkeypatch):
	settings_name = _get_odfl_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_odfl_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SOAP_RATE_NO_TOTAL_RESPONSE]))

	provider = OdflLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is None


# ---------------------------------------------------------------------------
# schedule_ltl_pickup
# ---------------------------------------------------------------------------


@pytest.mark.order(316)
def test_odfl_schedule_ltl_pickup_sets_awb_number(monkeypatch):
	settings_name = _get_odfl_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_odfl_token(settings_name)
	_create_odfl_accepted_quotation(shipment)

	shared_client = MockHttpxClient([_EBOL_RESPONSE, _PICKUP_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	OdflLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-ODFL-001"


@pytest.mark.order(318)
def test_odfl_schedule_ltl_pickup_sets_pickup_id(monkeypatch):
	settings_name = _get_odfl_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_odfl_token(settings_name)
	_create_odfl_accepted_quotation(shipment)

	shared_client = MockHttpxClient([_EBOL_RESPONSE, _PICKUP_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	OdflLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("pickup_id") == "PICKUP-ODFL-001"


@pytest.mark.order(320)
def test_odfl_schedule_ltl_pickup_attaches_bol(monkeypatch):
	settings_name = _get_odfl_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_odfl_token(settings_name)
	_create_odfl_accepted_quotation(shipment)

	shared_client = MockHttpxClient([_EBOL_RESPONSE, _PICKUP_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)

	OdflLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Shipment", "attached_to_name": shipment.name},
		fields=["file_name"],
	)
	assert len(attachments) >= 1


# ---------------------------------------------------------------------------
# cancel_shipment
# ---------------------------------------------------------------------------


@pytest.mark.order(322)
def test_odfl_cancel_shipment_cancels_pickup_and_bol(monkeypatch):
	settings_name = _get_odfl_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "awb_number", "PRO-ODFL-001")
	frappe.db.set_value("Shipment", shipment.name, "pickup_id", "PICKUP-ODFL-001")
	shipment.reload()
	_inject_odfl_token(settings_name)

	monkeypatch.setattr(
		"httpx.Client",
		lambda: MockHttpxClient([_DELETE_OK_RESPONSE, _DELETE_OK_RESPONSE]),
	)

	provider = OdflLTL()
	result = provider.cancel_shipment(shipment, settings_name=settings_name)

	assert result is not None
	assert "PICKUP-ODFL-001" in str(result) or "PRO-ODFL-001" in str(result)

	frappe.db.set_value("Shipment", shipment.name, "awb_number", None)
	frappe.db.set_value("Shipment", shipment.name, "pickup_id", None)


# ---------------------------------------------------------------------------
# track_shipment
# ---------------------------------------------------------------------------


@pytest.mark.order(324)
def test_odfl_track_shipment_returns_status(monkeypatch):
	settings_name = _get_odfl_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "awb_number", "PRO-ODFL-001")
	shipment.reload()
	_inject_odfl_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_TRACKING_RESPONSE]))

	provider = OdflLTL()
	result = provider.track_shipment(shipment, settings_name=settings_name)

	assert "IN_TRANSIT" in str(result)

	frappe.db.set_value("Shipment", shipment.name, "awb_number", None)


# ---------------------------------------------------------------------------
# get_documents
# ---------------------------------------------------------------------------


@pytest.mark.order(326)
def test_odfl_get_documents_returns_list(monkeypatch):
	settings_name = _get_odfl_settings_name()

	shipment = get_draft_ltl_shipment_for_tests()
	frappe.db.set_value("Shipment", shipment.name, "awb_number", "PRO-ODFL-001")
	shipment.reload()
	_inject_odfl_token(settings_name)

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_DOCUMENTS_RESPONSE]))

	provider = OdflLTL()
	docs = provider.get_documents(shipment, settings_name=settings_name)

	assert len(docs) >= 1
	doc_type = docs[0].get("document_type") or docs[0].get("documentType") or ""
	assert "BOL" in doc_type

	frappe.db.set_value("Shipment", shipment.name, "awb_number", None)


# ---------------------------------------------------------------------------
# Static / metadata methods
# ---------------------------------------------------------------------------


@pytest.mark.order(328)
def test_odfl_list_ltl_carriers_returns_odfl():
	provider = OdflLTL()
	carriers = provider.list_ltl_carriers()
	assert len(carriers) >= 1
	codes = [c.get("carrier_code") or c.get("scac") or "" for c in carriers]
	assert "ODFL" in codes


@pytest.mark.order(330)
def test_odfl_get_carrier_service_levels_includes_standard():
	shipment = get_draft_ltl_shipment_for_tests()
	provider = OdflLTL()
	levels = provider.get_carrier_service_levels(shipment)
	values = [s.get("value") or s.get("service_code") or "" for s in levels]
	assert "LTL" in values


@pytest.mark.order(332)
def test_odfl_validate_missing_pickup_date_returns_message():
	doc = MagicMock()
	doc.get = lambda key, default=None: (
		"some-address"
		if key in ("pickup_address_name", "delivery_address_name")
		else (["row"] if key == "shipment_delivery_note" else None)
	)
	provider = OdflLTL()
	result = provider.validate_required_shipment_form_fields(doc)
	assert result is not None
	assert len(result) > 0


@pytest.mark.order(334)
def test_odfl_validate_complete_doc_returns_none():
	shipment = get_draft_ltl_shipment_for_tests()
	provider = OdflLTL()
	result = provider.validate_required_shipment_form_fields(shipment)
	assert result is None


@pytest.mark.order(340)
def test_odfl_full_story_customer_delivery_shipment(monkeypatch):
	"""
	Complete dispatch lifecycle on the standard customer-delivery LTL shipment:
	quote → accept → schedule pickup → track → get documents → cancel.

	Verifies that the referenceNumber written by get_ltl_quotes is the value
	passed to schedule_ltl_pickup, and that awb_number / pickup_id written
	there are consumed correctly by track, get_documents, and cancel.
	"""
	settings_name = _get_odfl_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	_inject_odfl_token(settings_name)

	# Step 1: quote
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SOAP_RATE_RESPONSE]))
	OdflLTL().get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id", "carrier_scac"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_id"] == "REF-ODFL-001"
	assert saved[0]["carrier_scac"] == "ODFL"

	# Step 2: accept — submit the SQ (dispatcher action) and record it
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])
	_inject_odfl_token(settings_name)

	# Step 3: schedule pickup
	shared_client = MockHttpxClient([_EBOL_RESPONSE, _PICKUP_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	OdflLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-ODFL-001"
	assert shipment.get("pickup_id") == "PICKUP-ODFL-001"

	# Step 4: track — uses awb_number written by schedule
	_inject_odfl_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_TRACKING_RESPONSE]))
	track_result = OdflLTL().track_shipment(shipment, settings_name=settings_name)
	assert "IN_TRANSIT" in str(track_result)

	# Step 5: get documents — uses awb_number written by schedule
	_inject_odfl_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_DOCUMENTS_RESPONSE]))
	docs = OdflLTL().get_documents(shipment, settings_name=settings_name)
	assert len(docs) >= 1

	# Step 6: cancel — uses awb_number and pickup_id written by schedule
	_inject_odfl_token(settings_name)
	shared_cancel = MockHttpxClient([_DELETE_OK_RESPONSE, _DELETE_OK_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_cancel)
	cancel_result = OdflLTL().cancel_shipment(shipment, settings_name=settings_name)
	assert cancel_result is not None

	frappe.db.set_value("Shipment", shipment.name, {"awb_number": None, "pickup_id": None})


@pytest.mark.order(342)
def test_odfl_full_story_freight_terminal_shipment(monkeypatch):
	"""
	Complete dispatch lifecycle on the freight-terminal LTL shipment
	(delivery_to_type="Contact"), which exercises the Contact address
	resolution path through get_address_and_contact_info.
	"""
	settings_name = _get_odfl_settings_name()

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
		{"awb_number": None, "pickup_id": None, "accepted_quotation": None},
	)
	shipment.reload()
	_inject_odfl_token(settings_name)

	# Step 1: quote
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([_SOAP_RATE_RESPONSE]))
	OdflLTL().get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id"],
	)
	assert len(saved) == 1
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])
	_inject_odfl_token(settings_name)

	# Step 2: schedule pickup
	shared_client = MockHttpxClient([_EBOL_RESPONSE, _PICKUP_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	OdflLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)

	shipment.reload()
	assert shipment.get("awb_number") == "PRO-ODFL-001"

	# Step 3: cancel
	_inject_odfl_token(settings_name)
	shared_cancel = MockHttpxClient([_DELETE_OK_RESPONSE, _DELETE_OK_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_cancel)
	cancel_result = OdflLTL().cancel_shipment(shipment, settings_name=settings_name)
	assert cancel_result is not None

	frappe.db.set_value(
		"Shipment",
		shipment.name,
		{"awb_number": None, "pickup_id": None, "accepted_quotation": None},
	)
