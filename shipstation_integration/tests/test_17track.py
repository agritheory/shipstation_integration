# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
import pytest

from shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track import (
	SeventeenTrackClient,
	build_seventeen_track_webhook_callback_uri,
	get_webhook_callback_uri,
	seventeentrack_webhook,
)
from shipstation_integration.tests.setup import (
	MOCK_API_KEY,
	SEED_TN_ONE_REF,
	SEED_TN_TWO_REF,
	SEED_TN_WEBHOOK,
	TEST_17TRACK_COMPANY,
	create_seventeen_track_settings,
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


def accepted_response(tracking_number: str) -> dict:
	return {"accepted": [{"number": tracking_number}], "rejected": []}


def rejected_response(tracking_number: str, message: str) -> dict:
	return {
		"accepted": [],
		"rejected": [{"number": tracking_number, "error": {"code": -18010012, "message": message}}],
	}


def new_tn(tracking_number: str) -> frappe.model.document.Document:
	tn = frappe.get_doc({"doctype": "Tracking Number", "tracking_number": tracking_number})
	tn.flags.ignore_validate = True
	tn.insert(ignore_permissions=True)
	return tn


def test_get_webhook_callback_uri_matches_build_helper():
	assert get_webhook_callback_uri() == build_seventeen_track_webhook_callback_uri()


def test_build_webhook_callback_uri_includes_api_method_suffix():
	uri = build_seventeen_track_webhook_callback_uri()
	assert "/api/method/" in uri
	assert "seventeentrack_webhook" in uri


def test_webhook_callback_uri_stored_after_create_seventeen_track_settings():
	create_seventeen_track_settings()
	stored = frappe.db.get_value("Seventeen Track", TEST_17TRACK_COMPANY, "webhook_callback_uri")
	assert stored
	assert stored == build_seventeen_track_webhook_callback_uri()


def test_webhook_valid_signature():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()
	sig = make_signature(body_bytes, MOCK_API_KEY)

	name = frappe.db.get_value(
		"Tracking Number", {"tracking_number": SEED_TN_WEBHOOK, "docstatus": 1}, "name"
	)
	assert name, f"Seed Tracking Number {SEED_TN_WEBHOOK!r} not found (docstatus=1)"
	frappe.db.set_value(
		"Tracking Number", name, {"seventeen_track_status": None, "seventeen_track_description": None}
	)

	with patch.object(frappe.local, "request", mock_request(body_bytes, {"sign": sig}), create=True):
		seventeentrack_webhook()

	doc = frappe.get_doc("Tracking Number", name)
	assert doc.seventeen_track_status == "Delivered"
	assert doc.seventeen_track_description == "DELIVERED"
	assert doc.seventeen_track_latest_status_time == "2022-04-04T23:35:22Z"


def test_webhook_missing_signature():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()

	with patch("frappe.log_error") as mock_log, patch.object(
		frappe.local, "request", mock_request(body_bytes, {}), create=True
	):
		seventeentrack_webhook()

	mock_log.assert_called_once()
	assert "Missing Signature" in mock_log.call_args.kwargs.get("title", "")


def test_webhook_invalid_signature():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()

	with patch("frappe.log_error") as mock_log, patch.object(
		frappe.local, "request", mock_request(body_bytes, {"sign": "wrong_sig"}), create=True
	):
		seventeentrack_webhook()

	mock_log.assert_called_once()
	assert "Signature Mismatch" in mock_log.call_args.kwargs.get("title", "")


def test_webhook_ignores_non_tracking_updated_event():
	payload = json.dumps({"event": "AWB_UPDATED", "data": {"number": SEED_TN_WEBHOOK}}).encode()
	sig = make_signature(payload, MOCK_API_KEY)

	with patch("frappe.log_error") as mock_log, patch.object(
		frappe.local, "request", mock_request(payload, {"sign": sig}), create=True
	):
		seventeentrack_webhook()

	mock_log.assert_not_called()


def test_webhook_unknown_tracking_number():
	payload = json.dumps(
		{
			"event": "TRACKING_UPDATED",
			"data": {
				"number": "UNKNOWN-99999",
				"track_info": {
					"latest_status": {"status": "InTransit", "sub_status": "InTransit_PickedUp"},
					"latest_event": {"description": "Picked up", "time_utc": "2026-01-01T00:00:00Z"},
				},
			},
		}
	).encode()
	sig = make_signature(payload, MOCK_API_KEY)

	with patch("frappe.log_error") as mock_log, patch.object(
		frappe.local, "request", mock_request(payload, {"sign": sig}), create=True
	):
		seventeentrack_webhook()

	mock_log.assert_not_called()


def test_webhook_skips_stopped_tracking_number():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()
	sig = make_signature(body_bytes, MOCK_API_KEY)

	name = frappe.db.get_value(
		"Tracking Number", {"tracking_number": SEED_TN_WEBHOOK, "docstatus": 1}, "name"
	)
	assert name, f"Seed Tracking Number {SEED_TN_WEBHOOK!r} not found (docstatus=1)"

	frappe.db.set_value(
		"Tracking Number",
		name,
		{
			"subscription_status": "Stopped",
			"seventeen_track_status": None,
			"seventeen_track_description": None,
		},
	)

	with patch.object(frappe.local, "request", mock_request(body_bytes, {"sign": sig}), create=True):
		seventeentrack_webhook()

	doc = frappe.get_doc("Tracking Number", name)
	assert doc.seventeen_track_status is None, "Stopped TN should not be updated by webhook"
	assert doc.seventeen_track_description is None

	frappe.db.set_value("Tracking Number", name, "subscription_status", "Active")

	with patch.object(frappe.local, "request", mock_request(body_bytes, {"sign": sig}), create=True):
		seventeentrack_webhook()

	doc.reload()
	assert doc.seventeen_track_status == "Delivered"
	assert doc.seventeen_track_description == "DELIVERED"
	assert doc.seventeen_track_latest_status_time == "2022-04-04T23:35:22Z"


def test_submit_calls_register_tracks():
	tn = new_tn("TEST-SUBMIT-001")
	with patch.object(
		SeventeenTrackClient, "register_tracks", return_value=accepted_response("TEST-SUBMIT-001")
	) as mock_reg:
		tn.submit()
		mock_reg.assert_called_once_with([{"number": "TEST-SUBMIT-001"}])

	tn.reload()
	assert tn.subscription_status == "Active"


def test_submit_rejected_raises_error():
	tn = new_tn("TEST-REJECTED-001")
	with patch.object(
		SeventeenTrackClient,
		"register_tracks",
		return_value=rejected_response(
			"TEST-REJECTED-001", "The format of 'TEST-REJECTED-001' is invalid."
		),
	):
		with pytest.raises(frappe.exceptions.ValidationError, match="invalid"):
			tn.submit()


def test_cancel_calls_stop_tracking():
	tn = new_tn("TEST-CANCEL-001")
	with patch.object(
		SeventeenTrackClient, "register_tracks", return_value=accepted_response("TEST-CANCEL-001")
	):
		tn.submit()

	with patch.object(
		SeventeenTrackClient, "stop_tracking", return_value=accepted_response("TEST-CANCEL-001")
	) as mock_stop:
		tn.cancel()
		mock_stop.assert_called_once_with([{"number": "TEST-CANCEL-001"}])

	tn.reload()
	assert tn.subscription_status == "Stopped"


def test_trash_calls_delete_tracking():
	tn = new_tn("TEST-TRASH-001")
	with patch.object(
		SeventeenTrackClient, "register_tracks", return_value=accepted_response("TEST-TRASH-001")
	):
		tn.submit()
	with patch.object(
		SeventeenTrackClient, "stop_tracking", return_value=accepted_response("TEST-TRASH-001")
	):
		tn.cancel()

	with patch.object(
		SeventeenTrackClient, "delete_tracking", return_value=accepted_response("TEST-TRASH-001")
	) as mock_del:
		frappe.delete_doc("Tracking Number", tn.name, force=0)
		mock_del.assert_called_once_with([{"number": "TEST-TRASH-001"}])


def test_amend_same_number_calls_retrack():
	tn = new_tn("TEST-RETRACK-001")
	with patch.object(
		SeventeenTrackClient, "register_tracks", return_value=accepted_response("TEST-RETRACK-001")
	):
		tn.submit()
	with patch.object(
		SeventeenTrackClient, "stop_tracking", return_value=accepted_response("TEST-RETRACK-001")
	):
		tn.cancel()

	amended = frappe.copy_doc(tn)
	amended.amended_from = tn.name
	amended.docstatus = 0
	amended.flags.ignore_validate = True
	amended.insert(ignore_permissions=True)

	with patch.object(SeventeenTrackClient, "register_tracks") as mock_reg, patch.object(
		SeventeenTrackClient, "retrack", return_value=accepted_response("TEST-RETRACK-001")
	) as mock_ret:
		amended.submit()
		mock_ret.assert_called_once_with([{"number": "TEST-RETRACK-001"}])
		mock_reg.assert_not_called()


def test_amend_different_number_calls_register():
	tn = new_tn("TEST-AMEND-ORIG-001")
	with patch.object(
		SeventeenTrackClient, "register_tracks", return_value=accepted_response("TEST-AMEND-ORIG-001")
	):
		tn.submit()
	with patch.object(
		SeventeenTrackClient, "stop_tracking", return_value=accepted_response("TEST-AMEND-ORIG-001")
	):
		tn.cancel()

	amended = frappe.copy_doc(tn)
	amended.amended_from = tn.name
	amended.tracking_number = "TEST-AMEND-NEW-001"
	amended.docstatus = 0
	amended.flags.ignore_validate = True
	amended.insert(ignore_permissions=True)

	with patch.object(
		SeventeenTrackClient, "register_tracks", return_value=accepted_response("TEST-AMEND-NEW-001")
	) as mock_reg, patch.object(SeventeenTrackClient, "retrack") as mock_ret:
		amended.submit()
		mock_reg.assert_called_once_with([{"number": "TEST-AMEND-NEW-001"}])
		mock_ret.assert_not_called()


def test_no_api_key_raises_error():
	from frappe.utils.password import remove_encrypted_password

	remove_encrypted_password("Seventeen Track", TEST_17TRACK_COMPANY, "api_key")
	frappe.db.commit()
	try:
		tn = new_tn("TEST-NOKEY-001")
		with pytest.raises(frappe.exceptions.ValidationError, match="not configured"):
			tn.submit()
	finally:
		create_seventeen_track_settings()


def test_duplicate_active_number_raises_error():
	tn = frappe.get_doc({"doctype": "Tracking Number", "tracking_number": SEED_TN_ONE_REF})
	with pytest.raises(frappe.exceptions.ValidationError, match="already active"):
		tn.insert(ignore_permissions=True)


def test_single_reference_persisted():
	name = frappe.db.get_value("Tracking Number", {"tracking_number": SEED_TN_ONE_REF}, "name")
	assert name, f"Seed TN {SEED_TN_ONE_REF!r} not found"

	doc = frappe.get_doc("Tracking Number", name)
	assert len(doc.references) == 1
	assert doc.references[0].reference_doctype == "Item"
	assert doc.references[0].document_name == "Gooseberry Pie"


def test_multiple_references_persisted():
	name = frappe.db.get_value("Tracking Number", {"tracking_number": SEED_TN_TWO_REF}, "name")
	assert name, f"Seed TN {SEED_TN_TWO_REF!r} not found"

	doc = frappe.get_doc("Tracking Number", name)
	assert len(doc.references) == 2
	ref_names = {r.document_name for r in doc.references}
	assert ref_names == {"Ambrosia Pie", "Double Plum Pie"}
