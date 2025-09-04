# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

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
	def track_shipment_id(self, shipment_id: str | None = None, carrier: str | None = None) -> None:
		self.action("track", shipment_id, carrier)

	def stop_tracking(self, shipment_id: str | None = None, carrier: str | None = None) -> None:
		self.action("stop", shipment_id, carrier)

	def retrack(self, shipment_id: str | None = None, carrier: str | None = None) -> None:
		self.action("retrack", shipment_id, carrier)

	def action(self, action: str, shipment_id: str | None = None, carrier: str | None = None) -> None:

		if not shipment_id:
			return

		if not self.api_key:
			frappe.throw(_("17Track API key is not configured."))

		client = SeventeenTrackClient(self.get_password("api_key"))
		tracks = [{"number": shipment_id, "carrier": carrier or ""}]

		if action == "track":
			client.register_tracks(tracks)
		elif action == "stop":
			client.stop_tracking(tracks)
		elif action == "retrack":
			client.retrack(tracks)


class SeventeenTrackAPIError(Exception):
	pass


class SeventeenTrackClient:

	BASE_URL = "https://api.17track.net/track/v2.4"

	def __init__(self, api_key: str):
		self.api_key = api_key
		self.headers = {"Content-Type": "application/json", "17token": self.api_key}

	def _request(self, method: str, endpoint: str, payload: dict | None = None) -> Any:
		url = f"{self.BASE_URL}{endpoint}"
		response = requests.request(method, url, headers=self.headers, json=payload)
		try:
			response.raise_for_status()
			data = response.json()
		except Exception:
			raise SeventeenTrackAPIError(f"Invalid response: {response.text}")

		if not response.ok or data.get("code") != 0:
			error_msg = data.get("msg", response.text)
			raise SeventeenTrackAPIError(f"API error: {error_msg}")

		return data.get("data")

	def register_tracks(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Register one or more track numbers.
		Each track is a dict like {"number": "...", "carrier": ...}
		'carrier' is optional.
		"""
		return self._request("POST", "/register", tracks)

	def stop_tracking(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Stop tracking one or more track numbers.
		Each item: {"number": "...", "carrier": ...} ('carrier' required)
		"""
		return self._request("POST", "/stoptrack", tracks)

	def retrack(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Retrack one or more track numbers.
		Each item: {"number": "...", "carrier": ...} ('carrier' required)
		"""
		return self._request("POST", "/retrack", tracks)

	def delete_tracking(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Delete one or more track numbers.
		Each item: {"number": "...", "carrier": ...} ('carrier' required)
		"""
		return self._request("POST", "/deletetrack", tracks)

	def get_tracking_info(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Get tracking info for one or more track numbers.
		Each item: {"number": "...", "carrier": ...}
		"""
		return self._request("POST", "/gettrackinfo", tracks)

	def get_quota(self) -> Any:
		"""
		Get quota information.
		"""
		return self._request("POST", "/getquota")


@frappe.whitelist(allow_guest=True)
def seventeentrack_webhook():
	data = frappe.local.form_dict or json.loads(frappe.local.request.data)

	if data.get("event") == "TRACKING_STOPPED":
		return

	tracking_number = data.get("data", {}).get("number")
	track_info = data.get("data", {}).get("track_info", {})
	carrier = data.get("data", {}).get("carrier", {})
	latest_status = track_info.get("latest_status", {})
	latest_event = track_info.get("latest_event", {})

	shipment_exists = frappe.db.exists("Shipment", {"shipment_id": tracking_number})
	if not shipment_exists:
		return

	shipment = frappe.get_doc("Shipment", shipment_exists)

	status = latest_status.get("status")
	sub_status = latest_status.get("sub_status")

	shipment.seventeen_track_status = status
	shipment.seventeen_track_status_description = latest_event.get("description")

	sub_status_desc = STATUS_DESCRIPTION_MAP.get((status, sub_status))
	if sub_status_desc:
		shipment.seventeen_track_sub_status = sub_status_desc

	shipment.seventeen_track_latest_status_time = latest_event.get("time_utc")

	if carrier and not shipment.seventeen_track_carrier:
		shipment.seventeen_track_carrier = carrier

	seventeen_track_settings = frappe.get_single("Seventeen Track")
	if seventeen_track_settings.add_updates_as_comments:
		shipment.add_comment(
			comment_type="Comment",
			text=_(
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

	shipment.save(ignore_permissions=True)
	frappe.db.commit()
