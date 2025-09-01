# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
from typing import Any, Dict, List, Optional

import frappe
import requests
from frappe.model.document import Document


class SeventeenTrack(Document):
	pass


class SeventeenTrackAPIError(Exception):
	pass


class SeventeenTrackClient:
	"""
	seventeen_track = frappe.get_single("Seventeen Track")
	client = SeventeenTrackClient(seventeen_track.get_password("api_key"))
	tracks = [{ "number": "SD205269262AR"}]
	result = client.get_tracking_info(tracks=tracks)
	print(result)
	"""

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
		return self._request("POST", "/retrack", tracks)

	def get_tracking_info(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Get tracking info for one or more track numbers.
		Each item: {"number": "...", "carrier": ...}
		"""
		return self._request("POST", "/gettrackinfo", tracks)


@frappe.whitelist(allow_guest=True)
def seventeentrack_webhook():
	"""
	/api/method/shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track.seventeentrack_webhook
	"""
	data = frappe.local.form_dict or json.loads(frappe.local.request.data)
	event = data.get("event")
	info = data.get("data")
	number = info.get("number")
	carrier = info.get("carrier")

	if event == "TRACKING_UPDATED":
		track_info = info.get("track_info")
		latest_status = track_info.get("latest_status")
		status = latest_status.get("status")
		sub_status = latest_status.get("sub_status")
