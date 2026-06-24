# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Parameterized full-story LTL tests across BaseLTL providers.

WWEX, Banyan, and ODFL: quote → accept → schedule pickup → track → documents → cancel.

TrafficTech: quote API only (booking not published); quote → accept → schedule raises.
"""

from __future__ import annotations

import json as json_lib
import time
from typing import Any

import frappe
import pytest

from shipstation_integration.shipstation_integration.freight_providers.banyan_ltl import BanyanLTL
from shipstation_integration.shipstation_integration.freight_providers.odfl_ltl import OdflLTL
from shipstation_integration.shipstation_integration.freight_providers.traffictech_ltl import (
	TrafficTechLTL,
)
from shipstation_integration.shipstation_integration.freight_providers.wwex_ltl import WwexLTL
from shipstation_integration.shipstation_integration.overrides.shipment import (
	save_selected_ltl_quotes,
)
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	get_freight_terminal_shipment_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


class MockResponse:
	def __init__(self, json_data=None, text="", status_code=200):
		self._json = json_data
		self.text = text
		self.status_code = status_code
		if text:
			self.content = text.encode("utf-8")
		elif json_data is not None:
			self.content = json_lib.dumps(json_data).encode("utf-8")
			if not self.text:
				self.text = self.content.decode("utf-8")
		else:
			self.content = b""
		self.request = None

	@property
	def is_error(self):
		return self.status_code >= 400

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


# --- WWEX ---
SHOP_FLOW_RESPONSE = MockResponse(
	json_data={
		"response": {
			"offerList": [
				{
					"offerId": "offer-wwex-001",
					"productTransactionId": "txn-wwex-001",
					"primaryVendor": {
						"preferredName": "Estes Express",
						"scac": "EXLA",
					},
					"offeredProductList": [
						{
							"shopRQShipment": {
								"timeInTransit": {
									"transitDays": 3,
									"serviceDescription": "Standard LTL",
								}
							}
						}
					],
					"totalOfferPrice": {"value": "425.50", "unit": "USD"},
				}
			],
		}
	}
)

QUOTE_ORDER_FLOW_RESPONSE = MockResponse(
	json_data={
		"response": {
			"bolNumber": "BOL-WWEX-001",
			"proNumber": "PRO-WWEX-001",
			"pickupTxnId": "pickup-wwex-001",
		}
	}
)

DOCUMENT_DOWNLOAD_FLOW_RESPONSE = MockResponse(
	json_data={
		"response": {
			"documents": [{"documentType": "BILL_OF_LADING", "content": "JVBERi0=", "fileName": "bol.pdf"}]
		}
	}
)

SEARCH_SHIPMENTS_RESPONSE = MockResponse(
	json_data={"response": {"status": "IN_TRANSIT", "proNumber": "PRO-WWEX-001"}}
)

CANCEL_FLOW_RESPONSE = MockResponse(
	json_data={"response": {"confirmationNumber": "CANCEL-WWEX-001"}}
)


def get_wwex_settings_name():
	supplier = frappe.db.get_value(
		"Supplier", {"supplier_name": "WWEX LTL", "is_transporter": 1}, "name"
	)
	if not supplier:
		return None
	return frappe.db.get_value(
		"Freight Carrier Settings",
		{"company": "Ambrosia Pie Company", "supplier": supplier},
		"name",
	)


def inject_wwex_token(fc_name):
	frappe.cache.set_value(
		f"wwex_token:{fc_name}",
		{"access_token": "test-wwex-bearer-token", "expires_at": time.time() + 7200},
	)


# --- Banyan ---
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

BOOK_RESPONSE = MockResponse(json_data={"proNumber": "PRO-BAN-001", "bolNumber": "BOL-BAN-001"})

ALREADY_BOOKED_RESPONSE = MockResponse(
	status_code=400,
	json_data={
		"type": "https://httpstatuses.io/400",
		"title": "Bad Request",
		"status": 400,
		"detail": "Specified shipment '88508955' cannot be booked. Shipment is already Booked.",
	},
)

GET_SHIPMENT_BOOKED_RESPONSE = MockResponse(
	json_data={
		"loadId": 88508955,
		"status": "Booked",
		"manifestId": "PRO-BAN-ALREADY",
		"pickupNumber": "1677520735",
	}
)

DOCUMENTS_RESPONSE = MockResponse(
	json_data=[{"documentType": "BOL", "content": "JVBERi0=", "fileName": "bol_banyan.pdf"}]
)

TRACKING_RESPONSE_BANYAN = MockResponse(
	json_data={"status": "DELIVERED", "loadId": "load-banyan-001"}
)

CANCEL_RESPONSE_BANYAN = MockResponse(json_data={"confirmationNumber": "CANCEL-BAN-001"})


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


# --- ODFL ---
SOAP_RATE_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <ns2:getLTLRateEstimateResponse xmlns:ns2="http://myRate.ws.odfl.com/">
      <return>
        <referenceNumber>REF-ODFL-001</referenceNumber>
        <success>true</success>
        <rateEstimate>
          <netFreightCharge>480.00</netFreightCharge>
          <grossFreightCharge>600.00</grossFreightCharge>
          <fuelSurcharge>72.00</fuelSurcharge>
        </rateEstimate>
        <destinationCities>
          <name>Los Angeles</name>
          <serviceDays>2</serviceDays>
        </destinationCities>
      </return>
    </ns2:getLTLRateEstimateResponse>
  </soapenv:Body>
</soapenv:Envelope>"""

SOAP_RATE_RESPONSE = MockResponse(text=SOAP_RATE_XML)

SOAP_FAULT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
  <soapenv:Body>
    <soapenv:Fault>
      <faultcode>soapenv:Server</faultcode>
      <faultstring>Invalid account number</faultstring>
    </soapenv:Fault>
  </soapenv:Body>
</soapenv:Envelope>"""

SOAP_FAULT_RESPONSE = MockResponse(text=SOAP_FAULT_XML)

EBOL_RESPONSE = MockResponse(
	json_data={
		"proNumber": "PRO-ODFL-001",
		"bolNumber": "BOL-ODFL-001",
		"images": {"bol": "JVBERi0="},
	}
)

PICKUP_RESPONSE = MockResponse(json_data={"pickupConfirmationNumber": "PICKUP-ODFL-001"})

TRACKING_RESPONSE_ODFL = MockResponse(
	json_data={"proNumber": "PRO-ODFL-001", "status": "IN_TRANSIT"}
)

DELETE_OK_RESPONSE = MockResponse(json_data={"cancelled": True})


def get_odfl_settings_name():
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


def inject_odfl_token(fc_name):
	frappe.cache.set_value(
		f"odfl_token:{fc_name}",
		{"access_token": "odfl-bearer-token-001", "expires_at": time.time() + 7200},
	)


# --- TrafficTech ---
LTL_RATE_RESPONSE = MockResponse(
	json_data={
		"status": "OK",
		"error": None,
		"loadId": "tt-load-001",
		"quotes": [
			{
				"quoteId": "Q-100",
				"carrierName": "Example Carrier",
				"scac": "EXLA",
				"customerPrice": 450.0,
				"currencyCode": "USD",
				"transitTime": 3,
				"transitTimeDescription": "3 business days",
			}
		],
	}
)


def get_traffictech_settings_name():
	supplier = frappe.db.get_value(
		"Supplier", {"supplier_name": "TrafficTech LTL", "is_transporter": 1}, "name"
	)
	if not supplier:
		return None
	return frappe.db.get_value(
		"Freight Carrier Settings",
		{"company": "Ambrosia Pie Company", "supplier": supplier},
		"name",
	)


def clear_freight_terminal_quotation_state(shipment):
	for sq in frappe.get_all("Shipment Quotation", filters={"shipment": shipment.name}, pluck="name"):
		sq_doc = frappe.get_doc("Shipment Quotation", sq)
		if sq_doc.docstatus == 1:
			sq_doc.flags.ignore_permissions = True
			sq_doc.cancel()
		frappe.delete_doc("Shipment Quotation", sq, force=True)
	frappe.db.set_value(
		"Shipment",
		shipment.name,
		{"awb_number": None, "shipment_id": None, "pickup_id": None, "accepted_quotation": None},
	)
	shipment.reload()


def delete_all_quotations(shipment: Any) -> None:
	for sq in frappe.get_all("Shipment Quotation", filters={"shipment": shipment.name}, pluck="name"):
		sq_doc = frappe.get_doc("Shipment Quotation", sq)
		if sq_doc.docstatus == 1:
			sq_doc.flags.ignore_permissions = True
			sq_doc.cancel()
		frappe.delete_doc("Shipment Quotation", sq, force=True)


def run_wwex_story(
	shipment, settings_name: str, monkeypatch: pytest.MonkeyPatch, *, freight_terminal: bool = False
) -> None:
	inject_wwex_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHOP_FLOW_RESPONSE]))
	WwexLTL().get_ltl_quotes(shipment, settings_name=settings_name)
	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_id"] == "offer-wwex-001"
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])
	inject_wwex_token(settings_name)
	shared_client = MockHttpxClient([QUOTE_ORDER_FLOW_RESPONSE, DOCUMENT_DOWNLOAD_FLOW_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	WwexLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)
	shipment.reload()
	assert shipment.get("awb_number") == "PRO-WWEX-001"
	assert shipment.get("shipment_id") == "txn-wwex-001"
	if not freight_terminal:
		inject_wwex_token(settings_name)
		monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SEARCH_SHIPMENTS_RESPONSE]))
		assert "IN_TRANSIT" in str(WwexLTL().track_shipment(shipment, settings_name=settings_name))
		inject_wwex_token(settings_name)
		monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([DOCUMENT_DOWNLOAD_FLOW_RESPONSE]))
		assert len(WwexLTL().get_documents(shipment, settings_name=settings_name)) >= 1
	inject_wwex_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([CANCEL_FLOW_RESPONSE]))
	assert "CANCEL-WWEX-001" in str(WwexLTL().cancel_shipment(shipment, settings_name=settings_name))


def run_banyan_story(
	shipment, settings_name: str, monkeypatch: pytest.MonkeyPatch, *, freight_terminal: bool = False
) -> None:
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_RESPONSE]))
	BanyanLTL().get_ltl_quotes(shipment, settings_name=settings_name)
	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_transaction_id"] == "load-banyan-001"
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])
	shared_client = MockHttpxClient([BOOK_RESPONSE, DOCUMENTS_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)
	shipment.reload()
	assert shipment.get("awb_number") == "PRO-BAN-001"
	assert shipment.get("shipment_id") == "load-banyan-001"
	if not freight_terminal:
		monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([TRACKING_RESPONSE_BANYAN]))
		assert "DELIVERED" in str(BanyanLTL().track_shipment(shipment, settings_name=settings_name))
		monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([DOCUMENTS_RESPONSE]))
		assert len(BanyanLTL().get_documents(shipment, settings_name=settings_name)) >= 1
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([CANCEL_RESPONSE_BANYAN]))
	assert "CANCEL-BAN-001" in str(BanyanLTL().cancel_shipment(shipment, settings_name=settings_name))


@pytest.mark.order(61)
def test_banyan_schedule_pickup_already_booked_syncs(monkeypatch):
	settings_name = get_banyan_settings_name()
	assert settings_name, "Missing Banyan Freight Carrier Settings"

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	assert shipment.docstatus == 1
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_RESPONSE]))
	BanyanLTL().get_ltl_quotes(shipment, settings_name=settings_name)
	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		pluck="name",
	)
	assert saved
	frappe.get_doc("Shipment Quotation", saved[0]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0])

	shared_client = MockHttpxClient(
		[ALREADY_BOOKED_RESPONSE, GET_SHIPMENT_BOOKED_RESPONSE, DOCUMENTS_RESPONSE]
	)
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	msg = BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)
	shipment.reload()
	assert shipment.get("awb_number") == "PRO-BAN-ALREADY"
	assert shipment.get("shipment_id") == "load-banyan-001"
	assert shipment.get("pickup_id") == "1677520735"
	assert shipment.get("status") == "Booked"
	assert "already booked" in msg.lower()


@pytest.mark.order(62)
def test_get_ltl_quotes_requires_submitted_shipment():
	settings_name = get_banyan_settings_name()
	assert settings_name, "Missing Banyan Freight Carrier Settings"

	submitted = get_draft_ltl_shipment_for_tests()
	draft = frappe.copy_doc(submitted)
	draft.insert(ignore_permissions=True)
	try:
		with pytest.raises(frappe.ValidationError) as exc_info:
			BanyanLTL().get_ltl_quotes(draft, settings_name=settings_name)
		assert "submit" in str(exc_info.value).lower()
	finally:
		frappe.delete_doc("Shipment", draft.name, force=True)


@pytest.mark.order(63)
def test_banyan_schedule_pickup_persists_on_submitted_shipment(monkeypatch):
	settings_name = get_banyan_settings_name()
	assert settings_name, "Missing Banyan Freight Carrier Settings"

	meta = frappe.get_meta("Shipment")
	for fieldname in ("pickup_id", "awb_number", "status"):
		assert meta.get_field(fieldname).allow_on_submit, fieldname

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	assert shipment.docstatus == 1
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SHIPMENTS_RESPONSE]))
	BanyanLTL().get_ltl_quotes(shipment, settings_name=settings_name)
	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		pluck="name",
	)
	assert saved
	frappe.get_doc("Shipment Quotation", saved[0]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0])

	shared_client = MockHttpxClient([BOOK_RESPONSE, DOCUMENTS_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	BanyanLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)
	shipment.reload()
	assert shipment.get("awb_number") == "PRO-BAN-001"
	assert shipment.get("shipment_id") == "load-banyan-001"
	assert shipment.get("status") == "Booked"


def run_odfl_story(
	shipment, settings_name: str, monkeypatch: pytest.MonkeyPatch, *, freight_terminal: bool = False
) -> None:
	frappe.db.set_value("Freight Carrier Settings", settings_name, "account_number", "123456789")
	inject_odfl_token(settings_name)
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SOAP_RATE_RESPONSE]))
	offers = OdflLTL().fetch_ltl_offers(shipment, settings_name=settings_name)
	assert len(offers) == 1
	assert offers[0]["offer_id"] == "REF-ODFL-001"
	assert offers[0]["carrier_scac"] == "ODFL"
	assert offers[0]["total_price"] == 552.0
	assert len(offers[0]["charges"]) >= 1
	save_selected_ltl_quotes(shipment.name, offers)
	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id", "carrier_scac"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_id"] == "REF-ODFL-001"
	assert saved[0]["carrier_scac"] == "ODFL"
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])
	inject_odfl_token(settings_name)
	shared_client = MockHttpxClient([EBOL_RESPONSE, PICKUP_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_client)
	OdflLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)
	shipment.reload()
	assert shipment.get("awb_number") == "PRO-ODFL-001"
	assert shipment.get("pickup_id") == "PICKUP-ODFL-001"
	if not freight_terminal:
		inject_odfl_token(settings_name)
		monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([TRACKING_RESPONSE_ODFL]))
		assert "IN_TRANSIT" in str(OdflLTL().track_shipment(shipment, settings_name=settings_name))
		inject_odfl_token(settings_name)
		monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([DOCUMENTS_RESPONSE]))
		assert len(OdflLTL().get_documents(shipment, settings_name=settings_name)) >= 1
	inject_odfl_token(settings_name)
	shared_cancel = MockHttpxClient([DELETE_OK_RESPONSE, DELETE_OK_RESPONSE])
	monkeypatch.setattr("httpx.Client", lambda: shared_cancel)
	assert OdflLTL().cancel_shipment(shipment, settings_name=settings_name) is not None


def run_traffictech_story(shipment, settings_name: str, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Quote + accept only; booking API not available on this provider."""
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([LTL_RATE_RESPONSE]))
	TrafficTechLTL().get_ltl_quotes(shipment, settings_name=settings_name)
	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["name", "quote_or_offer_id", "quote_or_offer_transaction_id"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_id"] == "Q-100"
	assert saved[0]["quote_or_offer_transaction_id"] == "tt-load-001"
	frappe.get_doc("Shipment Quotation", saved[0]["name"]).submit()
	frappe.db.set_value("Shipment", shipment.name, "accepted_quotation", saved[0]["name"])
	with pytest.raises(frappe.ValidationError):
		TrafficTechLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)


GET_SETTINGS = {
	"WWEX": get_wwex_settings_name,
	"Banyan": get_banyan_settings_name,
	"ODFL": get_odfl_settings_name,
	"TrafficTech": get_traffictech_settings_name,
}


def run_carrier_story(
	carrier: str,
	shipment,
	settings_name: str,
	monkeypatch: pytest.MonkeyPatch,
	*,
	freight_terminal: bool,
) -> None:
	if carrier == "WWEX":
		run_wwex_story(shipment, settings_name, monkeypatch, freight_terminal=freight_terminal)
	elif carrier == "Banyan":
		run_banyan_story(shipment, settings_name, monkeypatch, freight_terminal=freight_terminal)
	elif carrier == "ODFL":
		run_odfl_story(shipment, settings_name, monkeypatch, freight_terminal=freight_terminal)
	elif carrier == "TrafficTech":
		run_traffictech_story(shipment, settings_name, monkeypatch)
	else:
		raise AssertionError(f"unknown carrier {carrier}")


@pytest.mark.order(58)
def test_wwex_shop_flow_diagnostic_messages():
	payload = {
		"correlationId": "WWEX-ERP-bf2af04b-ff4b-4a84-a6a9-11982ffe8717",
		"clientStatus": {"success": True, "message": ""},
		"response": {
			"message": "No Offers created.",
			"offerList": [],
			"requestQuoteWarning": (
				"Please reach out to your support team and request a spot quote for additional carrier options."
			),
			"shopRS": {
				"ineligible": True,
				"ineligibleReason": (
					"There are no carriers available to/from that combination of zip codes. "
					"Please contact your representative."
				),
			},
		},
	}
	messages = WwexLTL().shop_flow_diagnostic_messages(payload)
	joined = " ".join(messages)
	assert "WWEX-ERP-bf2af04b" in joined
	assert "zip codes" in joined
	assert "No Offers created" in joined
	assert "spot quote" in joined


@pytest.mark.order(59)
def test_odfl_package_weight_to_pounds():
	assert OdflLTL.package_weight_to_pounds({"value": 400, "unit": "kilograms"}) == 882
	assert OdflLTL.package_weight_to_pounds({"value": 125, "unit": "pounds"}) == 125


@pytest.mark.order(59)
def test_odfl_fetch_ltl_offers(monkeypatch):
	settings_name = get_odfl_settings_name()
	assert settings_name, "Missing ODFL Freight Carrier Settings"

	frappe.db.set_value("Freight Carrier Settings", settings_name, "account_number", "123456789")
	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SOAP_RATE_RESPONSE]))

	offers = OdflLTL().fetch_ltl_offers(shipment, settings_name=settings_name)
	assert len(offers) == 1
	offer = offers[0]
	assert offer["offer_id"] == "REF-ODFL-001"
	assert offer["transaction_id"] == "REF-ODFL-001"
	assert offer["carrier_scac"] == "ODFL"
	assert offer["total_price"] == 552.0
	assert offer["transit_days"] == 2.0
	assert any(c["type"] == "Net freight" for c in offer["charges"])


@pytest.mark.order(59)
def test_odfl_fetch_ltl_offers_soap_fault(monkeypatch):
	settings_name = get_odfl_settings_name()
	assert settings_name, "Missing ODFL Freight Carrier Settings"

	frappe.db.set_value("Freight Carrier Settings", settings_name, "account_number", "123456789")
	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([SOAP_FAULT_RESPONSE]))

	with pytest.raises(frappe.ValidationError) as exc_info:
		OdflLTL().fetch_ltl_offers(shipment, settings_name=settings_name)
	assert "Invalid account number" in str(exc_info.value)


@pytest.mark.order(60)
@pytest.mark.parametrize("carrier", ["WWEX", "Banyan", "ODFL", "TrafficTech"])
@pytest.mark.parametrize("delivery_type", ["customer", "freight_terminal"])
def test_ltl_full_dispatch_lifecycle(carrier, delivery_type, monkeypatch):
	get_settings = GET_SETTINGS[carrier]
	settings_name = get_settings()
	assert settings_name, f"Missing Freight Carrier Settings for {carrier}"

	freight_terminal = delivery_type == "freight_terminal"
	if delivery_type == "customer":
		reset_ltl_shipment_quotation_test_state()
		shipment = get_draft_ltl_shipment_for_tests()
	else:
		shipment = get_freight_terminal_shipment_for_tests()
		clear_freight_terminal_quotation_state(shipment)

	try:
		run_carrier_story(
			carrier, shipment, settings_name, monkeypatch, freight_terminal=freight_terminal
		)
	finally:
		delete_all_quotations(shipment)
		cleanup = {
			"awb_number": None,
			"shipment_id": None,
			"pickup_id": None,
			"accepted_quotation": None,
		}
		if shipment.docstatus == 1:
			cleanup["status"] = "Submitted"
		frappe.db.set_value("Shipment", shipment.name, cleanup)
