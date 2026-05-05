# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
import pytest

from shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track import (
	seventeentrack_webhook,
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


def make_17track_api_response(tracking_number: str) -> MagicMock:
	"""Mock the raw HTTP response from the 17Track API for a successful register/retrack/stop."""
	resp = MagicMock()
	resp.ok = True
	resp.json.return_value = {
		"code": 0,
		"data": {"accepted": [{"number": tracking_number}], "rejected": []},
	}
	return resp


def new_tn(tracking_number: str) -> frappe.model.document.Document:
	tn = frappe.get_doc({"doctype": "Tracking Number", "tracking_number": tracking_number})
	tn.flags.ignore_validate = True
	tn.insert(ignore_permissions=True)
	return tn


@pytest.mark.order(20)
def test_webhook_updates_tracking_number():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()
	sig = make_signature(body_bytes, "mock_test_api_key_abc123")

	name = frappe.db.get_value(
		"Tracking Number", {"tracking_number": "1Z2617V10397725789", "docstatus": 1}, "name"
	)
	assert name, "Seed Tracking Number '1Z2617V10397725789' not found (docstatus=1)"
	frappe.db.set_value(
		"Tracking Number", name, {"seventeen_track_status": None, "seventeen_track_description": None}
	)

	with patch.object(frappe.local, "request", mock_request(body_bytes, {"sign": sig}), create=True):
		seventeentrack_webhook()

	doc = frappe.get_doc("Tracking Number", name)
	assert doc.seventeen_track_status == "Delivered"
	assert doc.seventeen_track_description == "DELIVERED"
	assert doc.seventeen_track_latest_status_time == "2022-04-04T23:35:22Z"


@pytest.mark.order(21)
def test_webhook_rejects_invalid_signature():
	body_bytes = WEBHOOK_MOCK_PATH.read_bytes()

	with patch("frappe.log_error") as mock_log, patch.object(
		frappe.local, "request", mock_request(body_bytes, {"sign": "wrong_sig"}), create=True
	):
		seventeentrack_webhook()

	mock_log.assert_called_once()
	assert "Signature Mismatch" in mock_log.call_args.kwargs.get("title", "")


@pytest.mark.order(22)
def test_submit_registers_and_cancel_stops():
	tn = new_tn("TEST-SUBMIT-CANCEL-001")
	with patch("requests.request") as mock_req:
		mock_req.return_value = make_17track_api_response("TEST-SUBMIT-CANCEL-001")
		tn.submit()
		assert mock_req.call_args[0][1].endswith("/register")
		assert mock_req.call_args[1]["json"] == [{"number": "TEST-SUBMIT-CANCEL-001"}]

	tn.reload()
	assert tn.subscription_status == "Active"

	with patch("requests.request") as mock_req:
		mock_req.return_value = make_17track_api_response("TEST-SUBMIT-CANCEL-001")
		tn.cancel()
		assert mock_req.call_args[0][1].endswith("/stoptrack")

	tn.reload()
	assert tn.subscription_status == "Stopped"


@pytest.mark.order(23)
@pytest.mark.parametrize(
	("same_number", "expect_retrack"),
	[
		(True, True),
		(False, False),
	],
)
def test_amend_reuses_or_registers(same_number: bool, expect_retrack: bool):
	orig_num = "TEST-AMEND-SAME-001" if same_number else "TEST-AMEND-ORIG-001"
	new_num = "TEST-AMEND-SAME-001" if same_number else "TEST-AMEND-NEW-001"

	tn = new_tn(orig_num)
	with patch("requests.request", return_value=make_17track_api_response(orig_num)):
		tn.submit()
	with patch("requests.request", return_value=make_17track_api_response(orig_num)):
		tn.cancel()

	amended = frappe.copy_doc(tn)
	amended.amended_from = tn.name
	amended.tracking_number = new_num
	amended.docstatus = 0
	amended.flags.ignore_validate = True
	amended.insert(ignore_permissions=True)

	with patch("requests.request") as mock_req:
		mock_req.return_value = make_17track_api_response(new_num)
		amended.submit()
		call_url = mock_req.call_args[0][1]
		if expect_retrack:
			assert call_url.endswith("/retrack")
		else:
			assert call_url.endswith("/register")


@pytest.mark.order(24)
def test_duplicate_number_rejected():
	tn = frappe.get_doc({"doctype": "Tracking Number", "tracking_number": "TRK-SEED-0000001"})
	with pytest.raises(frappe.exceptions.ValidationError, match="already active"):
		tn.insert(ignore_permissions=True)
