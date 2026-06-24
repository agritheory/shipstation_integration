# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe

from shipstation_integration.geocoding import build_geocode_query, nominatim_geocode
from shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track import (
	parse_event_time,
	resolve_coordinates,
	sync_tracking_events,
	seventeentrack_webhook,
)
from shipstation_integration.tests.setup import (
	MOCK_API_KEY,
	SEED_TN_WEBHOOK,
	SEED_TN_WEBHOOK_EVENTS,
	TEST_17TRACK_COMPANY,
)

WEBHOOK_MOCK_PATH = (
	Path(frappe.get_app_path("shipstation_integration"))
	/ "tests"
	/ "fixtures"
	/ "mock_17track_webhook.json"
)


def make_signature(body_bytes: bytes, api_key: str) -> str:
	content = body_bytes.decode("utf-8") + "/" + api_key
	return hashlib.sha256(content.encode("utf-8")).hexdigest()


def mock_request(body_bytes: bytes, headers: dict) -> MagicMock:
	req = MagicMock()
	req.data = body_bytes
	req.headers = headers
	return req


def webhook_fixture_track_info() -> dict:
	return json.loads(WEBHOOK_MOCK_PATH.read_text())["data"]["track_info"]


def seed_webhook_tn_name() -> str:
	return frappe.db.get_value(
		"Tracking Number", {"tracking_number": SEED_TN_WEBHOOK, "docstatus": 1}, "name"
	)


# ---------------------------------------------------------------------------
# Group 1 — parse_event_time
# ---------------------------------------------------------------------------


def test_parse_event_time_utc_z():
	assert parse_event_time("2022-04-04T23:35:22Z") == "2022-04-04 23:35:22"


def test_parse_event_time_none():
	assert parse_event_time(None) is None


def test_parse_event_time_with_offset():
	# split("+")[0] strips the +HH:MM part
	assert parse_event_time("2022-04-04T16:35:22+07:00") == "2022-04-04 16:35:22"


# ---------------------------------------------------------------------------
# Group 2 — build_geocode_query
# ---------------------------------------------------------------------------


def test_build_query_from_parts():
	assert (
		build_geocode_query({"city": "Ontario", "state": "CA", "country": "US"}) == "Ontario, CA, US"
	)


def test_build_query_falls_back_to_location():
	assert build_geocode_query({"location": "GASQUET, CA, US"}) == "GASQUET, CA, US"


def test_build_query_all_empty():
	assert build_geocode_query({}) == ""


# ---------------------------------------------------------------------------
# Group 3 — resolve_coordinates
# ---------------------------------------------------------------------------


def test_resolve_uses_api_coords():
	address = {"coordinates": {"latitude": 34.06, "longitude": -117.64}}
	lat, lon, source = resolve_coordinates(address)
	assert (lat, lon, source) == (34.06, -117.64, "API")


def test_resolve_geocodes_when_no_api_coords():
	address = {
		"city": "Ontario",
		"state": "CA",
		"country": "US",
		"coordinates": {"latitude": None, "longitude": None},
	}
	with patch("frappe.get_hooks", return_value=["some.geocode.hook"]), patch(
		"frappe.get_attr", return_value=lambda a: (34.06, -117.64)
	):
		lat, lon, source = resolve_coordinates(address, enable_geocoding=True)
	assert (lat, lon, source) == (34.06, -117.64, "Geocoded")


def test_resolve_skips_geocoding_when_disabled():
	address = {"city": "Ontario", "state": "CA", "country": "US"}
	with patch("frappe.get_hooks") as mock_hooks:
		lat, lon, source = resolve_coordinates(address, enable_geocoding=False)
		mock_hooks.assert_not_called()
	assert (lat, lon, source) == (None, None, "")


def test_resolve_returns_empty_when_hook_returns_none():
	address = {"city": "Ontario", "state": "CA", "country": "US"}
	with patch("frappe.get_hooks", return_value=["some.geocode.hook"]), patch(
		"frappe.get_attr", return_value=lambda a: None
	):
		lat, lon, source = resolve_coordinates(address, enable_geocoding=True)
	assert (lat, lon, source) == (None, None, "")


def test_resolve_returns_empty_with_no_hook_registered():
	address = {"city": "Ontario", "state": "CA", "country": "US"}
	with patch("frappe.get_hooks", return_value=[]):
		lat, lon, source = resolve_coordinates(address, enable_geocoding=True)
	assert (lat, lon, source) == (None, None, "")


# ---------------------------------------------------------------------------
# Group 4 — nominatim_geocode
# ---------------------------------------------------------------------------


def test_nominatim_returns_coordinates():
	mock_resp = MagicMock()
	mock_resp.json.return_value = [{"lat": "34.06", "lon": "-117.64"}]
	mock_resp.raise_for_status = MagicMock()
	with patch("shipstation_integration.geocoding.requests.get", return_value=mock_resp):
		result = nominatim_geocode({"city": "Ontario", "state": "CA", "country": "US"})
	assert result == (34.06, -117.64)


def test_nominatim_returns_none_on_empty():
	mock_resp = MagicMock()
	mock_resp.json.return_value = []
	mock_resp.raise_for_status = MagicMock()
	with patch("shipstation_integration.geocoding.requests.get", return_value=mock_resp):
		result = nominatim_geocode({"city": "Ontario", "state": "CA", "country": "US"})
	assert result is None


def test_nominatim_returns_none_on_http_error():
	with patch(
		"shipstation_integration.geocoding.requests.get", side_effect=Exception("network error")
	):
		result = nominatim_geocode({"city": "Ontario", "state": "CA", "country": "US"})
	assert result is None


# ---------------------------------------------------------------------------
# Group 5 — sync_tracking_events
# ---------------------------------------------------------------------------


def test_sync_appends_new_events():
	tn_name = seed_webhook_tn_name()
	assert tn_name, f"Seed Tracking Number {SEED_TN_WEBHOOK!r} not found (docstatus=1)"
	tn_doc = frappe.get_doc("Tracking Number", tn_name)
	tn_doc.tracking_number_event = []

	sync_tracking_events(tn_doc, webhook_fixture_track_info(), enable_geocoding=False)

	assert len(tn_doc.tracking_number_event) == 5


def test_sync_deduplicates_by_event_time():
	tn_name = seed_webhook_tn_name()
	tn_doc = frappe.get_doc("Tracking Number", tn_name)
	# Pre-populate one row matching the latest fixture event time
	tn_doc.tracking_number_event = [frappe._dict({"event_time": "2022-04-04 23:35:22"})]

	sync_tracking_events(tn_doc, webhook_fixture_track_info(), enable_geocoding=False)

	assert len(tn_doc.tracking_number_event) == 5


def test_sync_normalizes_datetime_for_dedup():
	tn_name = seed_webhook_tn_name()
	tn_doc = frappe.get_doc("Tracking Number", tn_name)
	# Frappe may return Datetime values from DB with microseconds
	tn_doc.tracking_number_event = [frappe._dict({"event_time": "2022-04-04 23:35:22.000000"})]

	sync_tracking_events(tn_doc, webhook_fixture_track_info(), enable_geocoding=False)

	assert len(tn_doc.tracking_number_event) == 5


def test_sync_skips_event_without_time():
	tn_name = seed_webhook_tn_name()
	tn_doc = frappe.get_doc("Tracking Number", tn_name)
	tn_doc.tracking_number_event = []

	track_info = {
		"tracking": {
			"providers": [
				{
					"provider": {"name": "TEST"},
					"events": [
						{
							"time_utc": None,
							"description": "No timestamp event",
							"stage": "InfoReceived",
							"address": {},
						}
					],
				}
			]
		}
	}
	sync_tracking_events(tn_doc, track_info, enable_geocoding=False)

	assert len(tn_doc.tracking_number_event) == 0


# ---------------------------------------------------------------------------
# Group 6 — Webhook integration
# ---------------------------------------------------------------------------


def test_webhook_populates_child_table():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()
	sig = make_signature(body_bytes, MOCK_API_KEY)

	tn_name = seed_webhook_tn_name()
	assert tn_name, f"Seed Tracking Number {SEED_TN_WEBHOOK!r} not found (docstatus=1)"
	frappe.db.delete("Tracking Number Event", {"parent": tn_name})
	frappe.db.set_value("Seventeen Track", TEST_17TRACK_COMPANY, "enable_geocoding", 0)

	try:
		with patch.object(frappe.local, "request", mock_request(body_bytes, {"sign": sig}), create=True):
			seventeentrack_webhook()

		tn_doc = frappe.get_doc("Tracking Number", tn_name)
		assert len(tn_doc.tracking_number_event) == 5
		assert all(row.provider == "UPS" for row in tn_doc.tracking_number_event)
		assert all(row.event_time is not None for row in tn_doc.tracking_number_event)
	finally:
		frappe.db.set_value("Seventeen Track", TEST_17TRACK_COMPANY, "enable_geocoding", 1)


def test_webhook_no_duplicate_events_on_second_call():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()
	sig = make_signature(body_bytes, MOCK_API_KEY)

	tn_name = seed_webhook_tn_name()
	assert tn_name, f"Seed Tracking Number {SEED_TN_WEBHOOK!r} not found (docstatus=1)"
	frappe.db.delete("Tracking Number Event", {"parent": tn_name})
	frappe.db.set_value("Seventeen Track", TEST_17TRACK_COMPANY, "enable_geocoding", 0)

	try:
		with patch.object(frappe.local, "request", mock_request(body_bytes, {"sign": sig}), create=True):
			seventeentrack_webhook()
			seventeentrack_webhook()

		tn_doc = frappe.get_doc("Tracking Number", tn_name)
		assert len(tn_doc.tracking_number_event) == 5
	finally:
		frappe.db.set_value("Seventeen Track", TEST_17TRACK_COMPANY, "enable_geocoding", 1)


def test_webhook_populates_last_coordinates():
	"""Webhook writes last_latitude / last_longitude / last_event_location from geocoded events."""
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()
	sig = make_signature(body_bytes, MOCK_API_KEY)

	tn_name = seed_webhook_tn_name()
	assert tn_name, f"Seed Tracking Number {SEED_TN_WEBHOOK!r} not found (docstatus=1)"

	# Clear last_* fields and events, then re-seed events that already carry coordinates.
	frappe.db.set_value(
		"Tracking Number",
		tn_name,
		{"last_latitude": "", "last_longitude": "", "last_event_location": ""},
	)
	frappe.db.delete("Tracking Number Event", {"parent": tn_name})
	tn_doc = frappe.get_doc("Tracking Number", tn_name)
	for event_data in SEED_TN_WEBHOOK_EVENTS:
		row = tn_doc.append("tracking_number_event", event_data)
		row.db_insert()
	frappe.db.set_value("Seventeen Track", TEST_17TRACK_COMPANY, "enable_geocoding", 0)

	try:
		# The webhook deduplicates all 5 events (already present); last_* is still derived
		# from the pre-existing geocoded rows.
		with patch.object(frappe.local, "request", mock_request(body_bytes, {"sign": sig}), create=True):
			seventeentrack_webhook()

		tn_doc = frappe.get_doc("Tracking Number", tn_name)
		# Latest geocoded event is Delivered in GASQUET (last in SEED_TN_WEBHOOK_EVENTS).
		assert tn_doc.last_latitude == "41.8399"
		assert tn_doc.last_longitude == "-123.9729"
		assert tn_doc.last_event_location == "GASQUET, CA, US"
	finally:
		frappe.db.set_value("Seventeen Track", TEST_17TRACK_COMPANY, "enable_geocoding", 1)
