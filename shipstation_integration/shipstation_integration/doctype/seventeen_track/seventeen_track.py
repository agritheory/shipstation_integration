# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import hashlib
import json
import re
from typing import Any

from urllib.parse import urljoin

import frappe
import requests  # type: ignore[import-untyped]
from frappe import _
from frappe.model.document import Document

from shipstation_integration.install import INTEGRATION_ROLE_17TRACK

SEVENTEEN_TRACK_WEBHOOK_METHOD = (
	"shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track."
	"seventeentrack_webhook"
)


def build_seventeen_track_webhook_callback_uri() -> str:
	path = f"/api/method/{SEVENTEEN_TRACK_WEBHOOK_METHOD}"
	return urljoin(frappe.utils.get_url(), path)


@frappe.whitelist()
def get_webhook_callback_uri():
	"""Public URL 17TRACK should call (same value stored on the document after save)."""
	return build_seventeen_track_webhook_callback_uri()


INTEGRATION_USER_NAME = "17Track"


def sync_17track_integration_user_roles(user_name: str) -> None:
	if INTEGRATION_ROLE_17TRACK not in frappe.get_roles(user_name):
		frappe.get_doc("User", user_name).add_roles(INTEGRATION_ROLE_17TRACK)


def get_seventeen_track_sub_status_description(status: str | None, sub_status: str | None) -> str:
	key = (status or "", sub_status or "")
	merged: dict[tuple[str, str], str] = {}
	providers = (frappe.get_hooks("seventeen_track_status_description_providers") or []) + (
		frappe.get_hooks("extend_seventeen_track_status_descriptions") or []
	)
	for provider in providers:
		extra = frappe.get_attr(provider)()
		if isinstance(extra, dict):
			merged.update(extra)
	return merged.get(key, "")


def resolve_company_from_tracking_number(tn: Document) -> str:
	for ref in tn.references or []:
		dt = ref.reference_doctype
		dn = ref.document_name
		if not dt or not dn:
			continue
		try:
			meta = frappe.get_meta(dt)
		except Exception:
			continue
		if not meta.has_field("company"):
			continue
		company = frappe.db.get_value(dt, dn, "company")
		if company:
			return company
	company = frappe.defaults.get_global_default("default_company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	if company:
		return company
	frappe.throw(
		_(
			"Cannot determine Company for this Tracking Number. Link a document that has a Company, or set a default company."
		)
	)
	raise RuntimeError("unreachable")  # frappe.throw always raises; satisfies mypy exhaustiveness


def get_seventeen_track_settings_for_company(company: str) -> "SeventeenTrack":
	if not company:
		frappe.throw(_("Company is required for 17Track settings."))
	if not frappe.db.exists("Seventeen Track", company):
		frappe.throw(_("No Seventeen Track settings found for company {0}.").format(company))
	return frappe.get_doc("Seventeen Track", company)


def find_seventeen_track_settings_matching_signature(
	raw_body: bytes, signature: str
) -> "SeventeenTrack | None":
	for name in frappe.get_all("Seventeen Track", pluck="name"):
		doc = frappe.get_doc("Seventeen Track", name)
		api_key = doc.get_password("api_key", raise_exception=False)
		if not api_key:
			continue
		if verify_webhook_signature(raw_body, api_key, signature):
			return doc
	return None


class SeventeenTrack(Document):
	def validate(self):
		self.webhook_callback_uri = build_seventeen_track_webhook_callback_uri()
		api_key = self.get_password("api_key", raise_exception=False)
		if not api_key:
			return
		try:
			SeventeenTrackClient(api_key).get_quota()
		except SeventeenTrackAPIError as e:
			frappe.throw(str(e))

	@frappe.whitelist()
	def fetch_quota(self):
		if not self.get_password("api_key", raise_exception=False):
			frappe.throw(_("17Track API key is not configured."))
		try:
			client = SeventeenTrackClient(self.get_password("api_key"))
			return client.get_quota()
		except SeventeenTrackAPIError as e:
			frappe.throw(str(e))

	@frappe.whitelist()
	def ensure_integration_user(self):
		if self.seventeen_track_user:
			sync_17track_integration_user_roles(self.seventeen_track_user)
			return self.seventeen_track_user
		if frappe.db.exists("User", INTEGRATION_USER_NAME):
			sync_17track_integration_user_roles(INTEGRATION_USER_NAME)
			self.db_set("seventeen_track_user", INTEGRATION_USER_NAME)
			return INTEGRATION_USER_NAME

		email = f"17track@{re.sub(r'[^a-zA-Z0-9.-]', '-', frappe.local.site or 'site')}.integration"
		if frappe.db.exists("User", {"email": email}):
			existing = frappe.db.get_value("User", {"email": email}, "name")
			sync_17track_integration_user_roles(existing)
			self.db_set("seventeen_track_user", existing)
			return existing

		user = frappe.new_doc("User")
		user.name = INTEGRATION_USER_NAME
		user.email = email
		user.first_name = INTEGRATION_USER_NAME
		user.full_name = INTEGRATION_USER_NAME
		user.send_welcome_email = 0
		user.time_zone = frappe.db.get_single_value("System Settings", "time_zone")
		if frappe.get_meta("User").has_field("user_type"):
			user.user_type = "System User"
		user.flags.ignore_permissions = True
		user.flags.ignore_password_policy = True
		user.insert()
		user.add_roles(INTEGRATION_ROLE_17TRACK)

		self.db_set("seventeen_track_user", INTEGRATION_USER_NAME)
		return INTEGRATION_USER_NAME

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

	def request(
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
		return self.request("POST", "/register", tracks)

	def stop_tracking(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Stop tracking one or more track numbers.
		Each item: {"number": "..."}
		"""
		return self.request("POST", "/stoptrack", tracks)

	def retrack(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Retrack one or more track numbers.
		Each item: {"number": "..."}
		"""
		return self.request("POST", "/retrack", tracks)

	def delete_tracking(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Delete one or more track numbers.
		Each item: {"number": "..."}
		"""
		return self.request("POST", "/deletetrack", tracks)

	def get_tracking_info(self, tracks: list[dict[str, Any]]) -> Any:
		"""
		Get tracking info for one or more track numbers.
		Each item: {"number": "..."}
		Note: not used — the integration relies on webhooks for status updates.
		"""
		return self.request("POST", "/gettrackinfo", tracks)

	def get_quota(self) -> Any:
		"""
		Get quota information.
		"""
		return self.request("POST", "/getquota")


def verify_webhook_signature(raw_body: bytes, api_key: str, signature: str) -> bool:
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

		settings = find_seventeen_track_settings_matching_signature(raw_body, signature)
		if not settings:
			frappe.log_error(
				title="17Track Webhook: Signature Mismatch",
				message="Signature verification failed (no matching API key).\n\nReceived 'sign' header: {}\n\nHeaders: {}\n\nBody: {}".format(
					signature, dict(frappe.local.request.headers), body_str
				),
			)
			return

		try:
			data = json.loads(body_str)
		except json.JSONDecodeError:
			data = dict(frappe.local.form_dict or {})

		if data.get("event") != "TRACKING_UPDATED":
			return

		tracking_number = data.get("data", {}).get("number")
		track_info = data.get("data", {}).get("track_info", {})
		latest_status = track_info.get("latest_status", {})
		latest_event = track_info.get("latest_event", {})

		if settings and settings.seventeen_track_user:
			frappe.set_user(settings.seventeen_track_user)

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

		sub_status_desc = get_seventeen_track_sub_status_description(status, sub_status)
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
