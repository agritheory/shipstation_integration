# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from shipstation_integration.shipstation_integration.freight_providers.traffictech_ltl import (
	TrafficTechLTL,
)
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


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

LTL_RATE_NO_QUOTES_RESPONSE = MockResponse(
	json_data={"status": "OK", "error": None, "loadId": "tt-load-002", "quotes": []}
)

LTL_RATE_FAIL_RESPONSE = MockResponse(json_data={"status": "ERR", "error": "Invalid postal code"})


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


@pytest.mark.order(208)
def test_traffictech_get_ltl_quotes_creates_shipment_quotations(monkeypatch):
	settings_name = get_traffictech_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([LTL_RATE_RESPONSE]))

	provider = TrafficTechLTL()
	provider.get_ltl_quotes(shipment, settings_name=settings_name)

	saved = frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": shipment.name},
		fields=["quote_or_offer_id", "quote_or_offer_transaction_id", "grand_total"],
	)
	assert len(saved) == 1
	assert saved[0]["quote_or_offer_id"] == "Q-100"
	assert saved[0]["quote_or_offer_transaction_id"] == "tt-load-001"
	assert saved[0]["grand_total"] == 450.0


@pytest.mark.order(210)
def test_traffictech_get_ltl_quotes_returns_message(monkeypatch):
	settings_name = get_traffictech_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([LTL_RATE_RESPONSE]))

	provider = TrafficTechLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is not None
	assert "1" in result


@pytest.mark.order(212)
def test_traffictech_get_ltl_quotes_no_quotes_returns_none(monkeypatch):
	settings_name = get_traffictech_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([LTL_RATE_NO_QUOTES_RESPONSE]))

	provider = TrafficTechLTL()
	result = provider.get_ltl_quotes(shipment, settings_name=settings_name)

	assert result is None


@pytest.mark.order(214)
def test_traffictech_fetch_ltl_offers_normalizes(monkeypatch):
	settings_name = get_traffictech_settings_name()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([LTL_RATE_RESPONSE]))

	offers = TrafficTechLTL().fetch_ltl_offers(shipment, settings_name=settings_name)

	assert len(offers) == 1
	o = offers[0]
	assert o["offer_id"] == "Q-100"
	assert o["transaction_id"] == "tt-load-001"
	assert o["carrier_scac"] == "EXLA"
	assert o["total_price"] == 450.0
	assert o["transit_days"] == 3


@pytest.mark.order(216)
def test_traffictech_get_ltl_quotes_failed_status_raises(monkeypatch):
	settings_name = get_traffictech_settings_name()

	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()

	monkeypatch.setattr("httpx.Client", lambda: MockHttpxClient([LTL_RATE_FAIL_RESPONSE]))

	with pytest.raises(frappe.ValidationError):
		TrafficTechLTL().get_ltl_quotes(shipment, settings_name=settings_name)


@pytest.mark.order(218)
def test_traffictech_schedule_ltl_pickup_raises():
	settings_name = get_traffictech_settings_name()
	shipment = get_draft_ltl_shipment_for_tests()

	with pytest.raises(frappe.ValidationError):
		TrafficTechLTL().schedule_ltl_pickup(shipment, settings_name=settings_name)


@pytest.mark.order(220)
def test_traffictech_book_shipment_raises():
	settings_name = get_traffictech_settings_name()
	shipment = get_draft_ltl_shipment_for_tests()

	with pytest.raises(frappe.ValidationError):
		TrafficTechLTL().book_shipment(shipment, settings_name=settings_name)
