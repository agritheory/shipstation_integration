# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import hashlib
import json
from typing import Any

import frappe
import requests
from frappe import _
from frappe.model.document import Document

STATUS_DESCRIPTION_MAP = {
	("NotFound", "NotFound_Other"): "The carrier didn't return any message.",
	("NotFound", "NotFound_InvalidCode"): "The tracking number is invalid.",
	("InfoReceived", "InfoReceived"): "",
	("InTransit", "InTransit_PickedUp"): "The carrier has collected the package from the sender.",
	("InTransit", "InTransit_Other"): "Other circumstances beyond the currently known sub-statuses.",
	("InTransit", "InTransit_Departure"): "Package has left the originating country/region's port.",
	(
		"InTransit",
		"InTransit_Arrival",
	): "Package has arrived at the destination country/region's port.",
	(
		"InTransit",
		"InTransit_CustomsProcessing",
	): "Your shipment is under the customs clearance process.",
	("InTransit", "InTransit_CustomsReleased"): "Import/Export customs clearance is completed.",
	(
		"InTransit",
		"InTransit_CustomsRequiringInformation",
	): "Related information is required for clearance.",
	("Expired", "Expired_Other"): "",
	("AvailableForPickup", "AvailableForPickup_Other"): "",
	("OutForDelivery", "OutForDelivery_Other"): "",
	(
		"DeliveryFailure",
		"DeliveryFailure_Other",
	): "Other circumstances beyond the currently known sub-statuses.",
	(
		"DeliveryFailure",
		"DeliveryFailure_NoBody",
	): "Unable to contact the recipient temporarily during the delivery process, resulting in delivery failure.",
	(
		"DeliveryFailure",
		"DeliveryFailure_Security",
	): "Package encountered security, customs clearance, or fee issues during delivery, resulting in delivery failure.",
	(
		"DeliveryFailure",
		"DeliveryFailure_Rejected",
	): "Recipient refused to accept the package for certain reasons, resulting in delivery failure.",
	(
		"DeliveryFailure",
		"DeliveryFailure_InvalidAddress",
	): "Delivery failure due to an incorrect recipient address.",
	("Delivered", "Delivered_Other"): "",
	("Exception", "Exception_Other"): "Other circumstances beyond the currently known sub-statuses.",
	("Exception", "Exception_Returning"): "Package is being returned to the sender.",
	("Exception", "Exception_Returned"): "Sender has successfully received the returned package.",
	(
		"Exception",
		"Exception_NoBody",
	): "Cannot find the recipient due to the abnormal recipient information discovered before delivery.",
	(
		"Exception",
		"Exception_Security",
	): "Abnormalities found before delivery, including security, customs clearance, or fee issues.",
	(
		"Exception",
		"Exception_Damage",
	): "The package was found damaged during the transportation process.",
	("Exception", "Exception_Rejected"): "Recipient refused to accept the package before delivery.",
	(
		"Exception",
		"Exception_Delayed",
	): "Possible delay beyond the original scheduled transit time due to various circumstances.",
	("Exception", "Exception_Lost"): "Package lost due to various circumstances.",
	(
		"Exception",
		"Exception_Destroyed",
	): "Package unable to be delivered for various reasons and subsequently destroyed.",
	("Exception", "Exception_Cancel"): "Shipment order was cancelled due to various circumstances.",
}


class SeventeenTrack(Document):
	def validate(self):
		if not self.get_password("api_key"):
			return
		try:
			SeventeenTrackClient(self.api_key).get_quota()
		except SeventeenTrackAPIError as e:
			frappe.throw(str(e))

	def track_shipment_id(self, tracking_number: str | None = None) -> None:
		self.action("track", tracking_number)

	def stop_tracking(self, tracking_number: str | None = None) -> None:
		self.action("stop", tracking_number)

	def retrack(self, tracking_number: str | None = None) -> None:
		self.action("retrack", tracking_number)

	def delete_tracking(self, tracking_number: str | None = None) -> None:
		self.action("delete", tracking_number)

	def action(self, action: str, tracking_number: str | None = None) -> None:
		if not tracking_number:
			return

		api_key = self.get_password("api_key", raise_exception=False)
		if not api_key:
			frappe.throw(_("17Track API key is not configured."))

		client = SeventeenTrackClient(api_key)
		tracks = [{"number": tracking_number}]

		try:
			if action == "track":
				result = client.register_tracks(tracks)
				rejected = (result or {}).get("rejected", [])
				if rejected:
					error_msg = rejected[0].get("error", {}).get("message", "Unknown error")
					frappe.throw(f"17Track rejected tracking number {tracking_number}: {error_msg}")
			elif action == "stop":
				client.stop_tracking(tracks)
			elif action == "retrack":
				client.retrack(tracks)
			elif action == "delete":
				client.delete_tracking(tracks)
		except SeventeenTrackAPIError as e:
			frappe.throw(str(e))


class SeventeenTrackAPIError(Exception):
	pass


class SeventeenTrackClient:

	BASE_URL = "https://api.17track.net/track/v2.4"

	def __init__(self, api_key: str):
		self.api_key = api_key
		self.headers = {"Content-Type": "application/json", "17token": self.api_key}

	def _request(
		self, method: str, endpoint: str, payload: list[dict[str, Any]] | dict[str, Any] | None = None
	) -> Any:
		url = f"{self.BASE_URL}{endpoint}"
		response = requests.request(method, url, headers=self.headers, json=payload)
		try:
			data = response.json()
		except Exception:
			raise SeventeenTrackAPIError(response.text)

		if not response.ok or data.get("code") != 0:
			errors = data.get("data", {}).get("errors", [])
			error_msg = errors[0].get("message") if errors else data.get("msg", response.text)
			raise SeventeenTrackAPIError(error_msg)

		return data.get("data")

	def register_tracks(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Register one or more track numbers.
		Each track is a dict like {"number": "..."}
		"""
		return self._request("POST", "/register", tracks)

	def stop_tracking(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Stop tracking one or more track numbers.
		Each item: {"number": "..."}
		"""
		return self._request("POST", "/stoptrack", tracks)

	def retrack(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Retrack one or more track numbers.
		Each item: {"number": "..."}
		"""
		return self._request("POST", "/retrack", tracks)

	def delete_tracking(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Delete one or more track numbers.
		Each item: {"number": "..."}
		"""
		return self._request("POST", "/deletetrack", tracks)

	def get_tracking_info(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Get tracking info for one or more track numbers.
		Each item: {"number": "..."}
		Note: not used — the integration relies on webhooks for status updates.
		"""
		return self._request("POST", "/gettrackinfo", tracks)

	def get_quota(self) -> Any:
		"""
		Get quota information.
		"""
		return self._request("POST", "/getquota")


@frappe.whitelist()
def get_quota():
	settings = frappe.get_single("Seventeen Track")
	if not settings.api_key:
		frappe.throw(_("17Track API key is not configured."))
	try:
		client = SeventeenTrackClient(settings.get_password("api_key"))
		return client.get_quota()
	except SeventeenTrackAPIError as e:
		frappe.throw(str(e))


def _verify_webhook_signature(raw_body: bytes, api_key: str, signature: str) -> bool:
	content = raw_body.decode("utf-8") + "/" + api_key
	return hashlib.sha256(content.encode("utf-8")).hexdigest() == signature


@frappe.whitelist(allow_guest=True)
def seventeentrack_webhook():
	try:
		raw_body = frappe.local.request.data
		signature = frappe.local.request.headers.get("sign")
		body_str = raw_body.decode("utf-8", errors="replace")

		if not signature:
			frappe.log_error(
				title="17Track Webhook: Missing Signature",
				message="Request received without 'sign' header.\n\nHeaders: {}\n\nBody: {}".format(
					dict(frappe.local.request.headers), body_str
				),
			)
			return

		settings = frappe.get_single("Seventeen Track")
		api_key = settings.get_password("api_key")

		if not api_key:
			frappe.log_error(
				title="17Track Webhook: API Key Not Configured",
				message="Cannot verify webhook signature without an API key.",
			)
			return

		if not _verify_webhook_signature(raw_body, api_key, signature):
			frappe.log_error(
				title="17Track Webhook: Signature Mismatch",
				message="Signature verification failed.\n\nReceived 'sign' header: {}\n\nHeaders: {}\n\nBody: {}".format(
					signature, dict(frappe.local.request.headers), body_str
				),
			)
			return

		data = frappe.local.form_dict or json.loads(raw_body)

		if data.get("event") != "TRACKING_UPDATED":
			return

		tracking_number = data.get("data", {}).get("number")
		track_info = data.get("data", {}).get("track_info", {})
		latest_status = track_info.get("latest_status", {})
		latest_event = track_info.get("latest_event", {})

		tn_name = frappe.db.get_value(
			"Tracking Number",
			{"tracking_number": tracking_number, "docstatus": 1, "subscription_status": "Active"},
			"name",
		)
		if not tn_name:
			return

		tn_doc = frappe.get_doc("Tracking Number", tn_name)

		status = latest_status.get("status")
		sub_status = latest_status.get("sub_status")

		tn_doc.seventeen_track_status = status
		tn_doc.seventeen_track_description = latest_event.get("description")

		sub_status_desc = STATUS_DESCRIPTION_MAP.get((status, sub_status), "")
		tn_doc.seventeen_track_sub_status = sub_status_desc

		tn_doc.seventeen_track_latest_status_time = latest_event.get("time_utc")

		if settings.add_updates_as_comments:
			tn_doc.add_comment(
				comment_type="Comment",
				text=(
					"**17TRACK Update**\n"
					"\n"
					"- **Status:** {status}\n"
					"- **Sub-status:** {sub_status}\n"
					"- **Sub-status description:** {sub_status_desc}\n"
					"- **Event Time:** {event_time}\n"
					"- **Description:** {description}"
				).format(
					status=latest_status.get("status"),
					sub_status=latest_status.get("sub_status"),
					sub_status_desc=sub_status_desc,
					event_time=latest_event.get("time_utc"),
					description=latest_event.get("description"),
				),
			)

		tn_doc.db_update()
		frappe.db.commit()
	except Exception:
		frappe.log_error("17Track Webhook Error", frappe.get_traceback())
