# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Banyan Technology (RIM LIVE Connect v3) LTL carrier integration.

Workflow
--------
1. ``get_ltl_quotes``
   POST /shipments  (``waitForRates: true``)
   Saves one Shipment Quotation per quote returned.
   ``quote_or_offer_id``             = Banyan ``quoteId`` (integer)
   ``quote_or_offer_transaction_id`` = Banyan ``loadId``

2. User accepts one quotation (submits SQ).

3. ``schedule_ltl_pickup``
   POST /shipments/{loadId}/book  (with ``quoteId``)
   Sets ``awb_number`` (PRO), attaches BOL.

4. ``get_documents``  → GET  /shipments/{loadId}/documents
5. ``track_shipment`` → GET  /tracking/statuses?loadId=...
6. ``cancel_shipment``→ POST /shipments/{loadId}/cancel

Authentication
--------------
OAuth 2.0 client-credentials flow via Frappe Connected App.
Token endpoint: ``https://ws.integration.banyantechnology.com/auth/connect/token``
(sibling to ``/api/v3``, not inside it; or the URL stored in ``app.token_uri``).

**Primary (RIM / Banyan):** ``client_id`` and ``client_secret`` on Freight Carrier
Settings → OAuth 2.0 client-credentials POST to ``/auth/connect/token``.

**Alternate:** If Banyan issues a long-lived Bearer string, store it in
``ltl_api_key`` instead; it is sent as-is and skips the token endpoint. Only
one path is used: ``ltl_api_key`` wins when set (non-empty).

**Connected App:** Optional; if ``connected_app`` is set on the FCS record,
its client credentials and ``token_uri`` override the direct FCS fields for
the token request.
"""
from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Any

import frappe
import httpx
from frappe import _
from frappe.utils import now
from frappe.utils.file_manager import save_file

from shipstation_integration.base_ltl import BaseLTL
from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.utils import get_shipment_company_for_ltl

if TYPE_CHECKING:
	from erpnext.stock.doctype.shipment.shipment import Shipment

TOKEN_REFRESH_BUFFER = 60

# Banyan accessorial code cross-reference (canonical field → Banyan code)
BANYAN_ACCESSORIAL_CODES: dict[str, str] = {
	"additional_insurance_or_excess_value": "FVC",
	"appointment_required_at_delivery": "APPTDEL",
	"appointment_required_at_pickup": "APPTP",
	"collect_on_delivery": "COD",
	"construction_site_delivery": "GCON",
	"construction_site_pickup": "GCONP",
	"inside_delivery": "IDEL",
	"inside_pickup": "IPU",
	"lift_gate_required_at_delivery": "CLFTG",
	"lift_gate_required_at_pickup": "CLFTGO",
	"limited_access_delivery": "LTDAD",
	"limited_access_pickup": "LTDAP",
	"marked_or_tagged": "MARK",
	"notify_before_delivery": "NTFYD",
	"over_dimension_excessive_weight": "OVR",
	"protect_from_cold": "PFF",
	"residential_delivery": "RESDEL",
	"residential_pickup": "RESP",
	"secured_limited_access_delivery": "SLTDAD",
	"secured_limited_access_pickup": "SLTDAP",
	"sort_and_segregate": "SRT",
	"tradeshow_delivery": "TRDSHWD",
	"tradeshow_pickup": "TRDSHWP",
}


class BanyanLTL(BaseLTL):
	"""Banyan Technology LIVE Connect v3 LTL provider."""

	def __init__(self):
		self.provider = "Banyan"

	def get_fcs(self, doc: Shipment | None, settings_name: str | None):
		if settings_name and frappe.db.exists("Freight Carrier Settings", settings_name):
			return frappe.get_doc("Freight Carrier Settings", settings_name)
		co = get_shipment_company_for_ltl(doc)
		supplier = getattr(doc, "preferred_carrier", None)
		fc = get_freight_carrier_settings(co, supplier) if co and supplier else None
		if not fc:
			frappe.throw(
				_("No Banyan Freight Carrier Settings found for company {0} and supplier {1}.").format(
					co, supplier
				)
			)
		return fc

	def get_token(self, fc) -> str:
		"""Return a bearer token via client_credentials grant.

		Priority:
		1. Static API key stored in ``ltl_api_key`` — returned as-is.
		2. ``client_id`` / ``client_secret`` on FCS — client_credentials grant to
		   ``/auth/connect/token``.
		3. Connected App — uses its client_id/client_secret.
		"""
		if hasattr(fc, "get_password"):
			static_key = fc.get_password("ltl_api_key", raise_exception=False) or ""
		else:
			static_key = fc.ltl_api_key or ""
		if static_key:
			return static_key

		cache_key = f"banyan_token:{fc.name}"
		cached = frappe.cache.get_value(cache_key)
		if cached and cached.get("expires_at", 0) > time.time() + TOKEN_REFRESH_BUFFER:
			return cached["access_token"]

		token_url = self.oauth_token_url(fc)

		client_id = fc.get_password("client_id") if hasattr(fc, "get_password") else (fc.client_id or "")
		client_secret = (
			fc.get_password("client_secret") if hasattr(fc, "get_password") else (fc.client_secret or "")
		)

		if client_id and client_secret and not getattr(fc, "connected_app", None):
			with httpx.Client() as client:
				resp = client.post(
					token_url,
					data={
						"grant_type": "client_credentials",
						"client_id": client_id,
						"client_secret": client_secret,
					},
					timeout=30,
				)
			self.raise_for_status(resp, "token")

		else:
			app = frappe.get_doc("Connected App", fc.connected_app)
			token_url = app.token_uri or token_url
			with httpx.Client() as client:
				resp = client.post(
					token_url,
					data={
						"grant_type": "client_credentials",
						"client_id": app.client_id,
						"client_secret": app.get_password("client_secret"),
					},
					timeout=30,
				)
			self.raise_for_status(resp, "token (connected app)")

		payload = resp.json()
		access_token = payload.get("access_token") or payload.get("token")
		expires_in = int(payload.get("expires_in", 3600))
		frappe.cache.set_value(
			cache_key,
			{"access_token": access_token, "expires_at": time.time() + expires_in},
			expires_in_sec=expires_in,
		)
		return access_token

	def headers(self, fc) -> dict:
		return {
			"Authorization": f"Bearer {self.get_token(fc)}",
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

	def api_base(self, fc) -> str:
		"""REST base including ``/api/v3``. Host-only URLs (common in desk) get ``/api/v3`` appended."""
		base = (fc.base_url or "https://ws.integration.banyantechnology.com/api/v3").rstrip("/")
		if "banyantechnology.com" in base and "/api/v3" not in base:
			base = f"{base}/api/v3"
		return base

	def oauth_token_url(self, fc) -> str:
		"""Banyan issues tokens at ``{host}/auth/connect/token``, not under ``/api/v3``."""
		api = self.api_base(fc)
		root = api[: -len("/api/v3")] if api.endswith("/api/v3") else api
		return f"{root.rstrip('/')}/auth/connect/token"

	def url(self, fc, path: str) -> str:
		return f"{self.api_base(fc)}/{path.lstrip('/')}"

	def raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
		"""Raise with Banyan's error body surfaced in the UI and error log."""
		if resp.is_error:
			try:
				body = resp.json()
			except Exception:
				body = resp.text
			detail = body if isinstance(body, str) else frappe.as_json(body, indent=2)
			label = f" ({context})" if context else ""
			# Include Allow header for 405 so we know which methods the endpoint accepts
			if resp.status_code == 405 and resp.headers.get("allow"):
				detail = f"Allowed methods: {resp.headers['allow']}\n\n{detail}"
			frappe.log_error(
				title=f"Banyan LTL {resp.status_code}{label}",
				message=detail,
			)
			frappe.throw(
				_("Banyan API error {0}{1}:\n{2}").format(resp.status_code, label, detail),
				title=_("Banyan LTL Error"),
			)

	@staticmethod
	def banyan_location(info: dict) -> dict:
		"""Convert get_address_and_contact_info output → Banyan PascalCase location block.

		Banyan requires both CompanyName and LocationName (separate from Address).
		ContactPerson firstName/lastName are split from the full name.
		"""
		addr = info["address"]
		contact = info["contact"]
		company = addr.get("company_name") or ""
		name_parts = (contact["name"] or "").split(" ", 1)
		return {
			"CompanyName": company,
			"LocationName": company,
			"Address": {
				"LineOne": addr.get("address_line1") or "",
				"LineTwo": addr.get("address_line2") or "",
				"City": addr.get("city_locality") or "",
				"StateOrProvince": addr.get("state_province") or "",
				"ZipCode": addr.get("postal_code") or "",
				"Country": "United States",
			},
			"ContactPerson": {
				"FirstName": name_parts[0] if name_parts else "",
				"LastName": name_parts[1] if len(name_parts) > 1 else "",
			},
			"ContactMethods": {
				"PhoneNumber": contact.get("phone_number") or "",
				"Email": contact.get("email") or "",
			},
		}

	SHIP_TYPE_MAP = {
		"Shipper": "Shipper",
		"Consignee": "Consignee",
		"Third Party": "ThirdParty",
	}
	PAY_TYPE_MAP = {
		"Prepaid": "Prepaid",
		"Collect": "Collect",
		"Third Party": "ThirdParty",
	}

	def validate_pickup_date(self, doc: Shipment) -> str:
		"""Return pickup_date as a string, throwing user-friendly errors if missing or past."""
		from frappe.utils import getdate, today

		pickup_date = doc.get("pickup_date")
		if not pickup_date:
			frappe.throw(_("Pickup Date is required for LTL shipments."), title=_("Missing Pickup Date"))
		if getdate(pickup_date) < getdate(today()):
			frappe.throw(
				_("Pickup Date {0} is in the past. Please update it to today or a future date.").format(
					pickup_date
				),
				title=_("Invalid Pickup Date"),
			)
		return str(pickup_date)

	def build_handling_units(self, doc: Shipment) -> list[dict]:
		"""Build Banyan HandlingUnits[] (PascalCase) from Shipment Delivery Note rows."""
		ltl = ShipstationLTL()
		packages = ltl.build_packages_from_sdn(doc)
		units = []
		for pkg in packages:
			dims = pkg.get("dimensions", {})
			# Banyan dim UOM: "IN" or "CM" — SDN stores inches by default
			dim_uom = "IN" if (dims.get("unit") or "inches").startswith("inch") else "CM"
			# Banyan weight UOM: "LBS" or "KGS"
			wt_uom = "KGS" if (pkg["weight"].get("unit") or "pounds").startswith("kilo") else "LBS"

			product = {
				"PackageType": pkg.get("code") or "Pallets",
				"Description": pkg.get("description") or "",
				"Class": str(int(pkg.get("freight_class") or 50)),
				"Weight": float(pkg["weight"]["value"]),
				"WeightUnitOfMeasurement": wt_uom,
				"Dimensions": {
					"Length": float(dims.get("length", 0)),
					"Width": float(dims.get("width", 0)),
					"Height": float(dims.get("height", 0)),
					"UnitOfMeasurement": dim_uom,
				},
				"Quantity": int(pkg.get("quantity", 1)),
			}
			if pkg.get("nmfc_code"):
				product["Nmfc"] = pkg["nmfc_code"]

			units.append(
				{
					"PackageType": pkg.get("code") or "Pallets",
					"Quantity": int(pkg.get("quantity", 1)),
					"Products": [product],
				}
			)
		return units

	def build_ez_rate_payload(self, doc: Shipment, fc) -> dict:
		"""Build the simpler Banyan POST /shipments/ezrate payload (zip-to-zip rating).

		EZ Rate skips full address, company name, location name, and BillTo validation.
		Only origin/destination postal codes, handling units, and service mode are required.
		"""
		pickup_date = self.validate_pickup_date(doc)
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)

		return {
			"ImportAsStatus": "Pending",
			"shouldRunRates": True,
			"waitForRates": True,
			"ShipmentData": {
				"ShipType": self.SHIP_TYPE_MAP.get(doc.get("billing_type") or "Shipper", "Shipper"),
				"PayType": self.PAY_TYPE_MAP.get(doc.get("payment_terms") or "Prepaid", "Prepaid"),
				"PickupDate": pickup_date,
				"ShipperLocation": {"Address": {"ZipCode": origin_info["address"].get("postal_code") or ""}},
				"ConsigneeLocation": {"Address": {"ZipCode": dest_info["address"].get("postal_code") or ""}},
				"HandlingUnits": self.build_handling_units(doc),
				"Accessorials": self.build_accessorial_list(doc),
				"ShipmentServices": [{"ServiceMode": "LTL", "Quantity": 1}],
				"ReferenceNumber": doc.name,
			},
		}

	def build_accessorial_list(self, doc: Shipment) -> list[str]:
		return [code for field, code in BANYAN_ACCESSORIAL_CODES.items() if doc.get(field)]

	def build_shipment_payload(self, doc: Shipment, fc) -> dict:
		"""Build the full Banyan POST /shipments payload.

		Banyan v3 wraps everything in ``ShipmentData`` with PascalCase field names.
		``ImportAsStatus`` = "Pending" requests rate retrieval without booking.
		``ShipType``  ← billing_type  (Shipper / Consignee / ThirdParty)
		``PayType``   ← payment_terms (Prepaid / Collect)
		``ShipmentServices`` must contain at least one entry; we always include "LTL".
		"""
		pickup_date = self.validate_pickup_date(doc)
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)

		billing_type = doc.get("billing_type") or "Shipper"
		payment_terms = doc.get("payment_terms") or "Prepaid"

		# BillTo: use shipper info for prepaid, consignee for collect
		bill_info = origin_info if billing_type != "Consignee" else dest_info
		bill_addr = bill_info["address"]
		bill_contact = bill_info["contact"]
		bill_to = {
			"CompanyName": bill_addr.get("company_name") or "",
			"Address": {
				"LineOne": bill_addr.get("address_line1") or "",
				"LineTwo": bill_addr.get("address_line2") or "",
				"City": bill_addr.get("city_locality") or "",
				"StateOrProvince": bill_addr.get("state_province") or "",
				"ZipCode": bill_addr.get("postal_code") or "",
				"Country": "United States",
			},
			"ContactMethods": {
				"PhoneNumber": bill_contact.get("phone_number") or "",
				"Email": bill_contact.get("email") or "",
			},
		}
		if fc.account_number:
			bill_to["AccountNumber"] = fc.account_number

		shipment_data = {
			"ShipType": self.SHIP_TYPE_MAP.get(billing_type, "Shipper"),
			"PayType": self.PAY_TYPE_MAP.get(payment_terms, "Prepaid"),
			"PickupDate": str(pickup_date),
			"ShipperLocation": self.banyan_location(origin_info),
			"ConsigneeLocation": self.banyan_location(dest_info),
			"BillTo": bill_to,
			"HandlingUnits": self.build_handling_units(doc),
			"Accessorials": self.build_accessorial_list(doc),
			# At least one ShipmentServiceDto is required; Quantity must be > 0
			"ShipmentServices": [{"ServiceMode": "LTL", "Quantity": 1}],
			"ReferenceNumber": doc.name,
		}

		# shouldRunRates / waitForRates are top-level flags (not inside ShipmentData)
		return {
			"ImportAsStatus": "Pending",
			"shouldRunRates": True,
			"waitForRates": True,
			"ShipmentData": shipment_data,
		}

	def fetch_banyan_offers(self, doc: Shipment, fc) -> tuple[str, list[dict]]:
		"""Call the Banyan API and return (load_id, raw_quotes) without saving."""
		use_ez = bool(getattr(fc, "use_ez_rate", False))
		endpoint = "/shipments/ezrate" if use_ez else "/shipments"
		payload = self.build_ez_rate_payload(doc, fc) if use_ez else self.build_shipment_payload(doc, fc)

		with httpx.Client() as client:
			resp = client.post(
				self.url(fc, endpoint),
				json=payload,
				headers=self.headers(fc),
				timeout=120,
			)
		self.raise_for_status(resp, f"get_ltl_quotes POST {endpoint}")
		data = resp.json()
		return data.get("loadId") or data.get("id") or "", data.get("quotes") or []

	def fetch_ltl_offers(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""Return Banyan quotes as normalized dicts without saving anything."""
		fc = self.get_fcs(doc, settings_name)
		load_id, quotes = self.fetch_banyan_offers(doc, fc)
		results: list[dict[str, Any]] = []
		for q in quotes:
			scac = q.get("scac") or ""
			raw_price = q.get("rawPrice") or {}
			results.append(
				{
					"carrier_name": q.get("carrierName") or "Banyan",
					"carrier_scac": scac,
					"offer_id": str(q.get("quoteId") or ""),
					"transaction_id": load_id,
					"service_level": q.get("serviceDescription") or "",
					"total_price": float(raw_price.get("netPrice") or raw_price.get("totalPrice") or 0),
					"currency": "USD",
					"transit_days": q.get("transitDays"),
					"estimated_delivery_date": None,
					"expiration_date": None,
					"is_spot_quote": False,
					"charges": [],
				}
			)
		return results

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""POST /shipments (or /shipments/ezrate) and save one Shipment Quotation per quote."""
		fc = self.get_fcs(doc, settings_name)
		load_id, quotes = self.fetch_banyan_offers(doc, fc)

		if not quotes:
			frappe.msgprint(_("Banyan returned no quotes for this shipment."))
			return None

		saved = 0
		for q in quotes:
			carrier_name = q.get("carrierName") or "Banyan"
			scac = q.get("scac") or ""
			quote_id = str(q.get("quoteId") or "")
			raw_price = q.get("rawPrice") or {}
			net = float(raw_price.get("netPrice") or raw_price.get("totalPrice") or 0)
			transit_days = q.get("transitDays")
			service_desc = q.get("serviceDescription") or ""

			supplier_name = (
				frappe.db.get_value("Supplier", {"ltl_carrier_scac": scac, "is_transporter": 1}, "name")
				if scac
				else None
			)

			sq = frappe.new_doc("Shipment Quotation")
			sq.shipment = doc.name
			sq.carrier = supplier_name or carrier_name
			sq.carrier_scac = scac
			sq.quote_or_offer_id = quote_id
			sq.quote_or_offer_transaction_id = load_id
			sq.service_level = service_desc
			sq.grand_total = net
			sq.pickup_date = doc.get("pickup_date")
			if transit_days is not None:
				sq.estimated_delivery_days = float(transit_days)
			sq.insert(ignore_permissions=True)
			saved += 1

		return _("{0} Banyan carrier quote(s) saved as Shipment Quotation(s).").format(saved)

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Book the accepted quote via POST /shipments/{loadId}/book."""
		fc = self.get_fcs(doc, settings_name)

		accepted_sq_name = doc.accepted_quotation or frappe.db.get_value(
			"Shipment Quotation", {"shipment": doc.name, "docstatus": 1}, "name"
		)
		if not accepted_sq_name:
			frappe.throw(_("No accepted Shipment Quotation found. Please accept a quote first."))

		sq = frappe.get_doc("Shipment Quotation", accepted_sq_name)
		load_id = sq.quote_or_offer_transaction_id
		quote_id = sq.quote_or_offer_id

		if not load_id or not quote_id:
			frappe.throw(_("The accepted Shipment Quotation is missing Banyan loadId/quoteId."))

		with httpx.Client() as client:
			resp = client.post(
				self.url(fc, f"/shipments/{load_id}/book"),
				json={"quoteId": int(quote_id)},
				headers=self.headers(fc),
				timeout=60,
			)
		self.raise_for_status(resp, "schedule_ltl_pickup POST /book")
		data = resp.json()

		pro_number = data.get("proNumber") or data.get("bolNumber") or ""
		dt, dn = doc.doctype, doc.name
		frappe.set_value(dt, dn, "carrier", doc.preferred_carrier)
		frappe.set_value(dt, dn, "carrier_service", sq.service_level)
		frappe.set_value(dt, dn, "awb_number", pro_number)
		frappe.set_value(dt, dn, "shipment_id", load_id)

		docs_saved = False
		try:
			docs = self.get_documents(doc, settings_name=fc.name)
			now_dt = now().split(".")[0]
			for d in docs:
				raw = base64.b64decode(d.get("content") or "")
				if raw:
					save_file(
						f"{doc.name}-{d.get('document_type','BOL')}-{now_dt}.pdf",
						raw,
						"Shipment",
						doc.name,
					)
			docs_saved = bool(docs)
		except Exception:
			frappe.log_error(
				title="Banyan: Error attaching documents",
				message=frappe.get_traceback(),
				reference_doctype="Shipment",
				reference_name=doc.name,
			)

		msg = _("Banyan booking confirmed. PRO: {0}").format(pro_number)
		if docs_saved:
			msg += " " + _("Documents have been attached.")
		return msg

	def cancel_shipment(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""POST /shipments/{loadId}/cancel."""
		fc = self.get_fcs(doc, settings_name)
		load_id = doc.get("shipment_id") or ""
		if not load_id:
			frappe.throw(_("No Banyan loadId (shipment_id) found on this Shipment."))

		with httpx.Client() as client:
			resp = client.post(
				self.url(fc, f"/shipments/{load_id}/cancel"),
				json={},
				headers=self.headers(fc),
				timeout=30,
			)
		self.raise_for_status(resp, "cancel_shipment POST /cancel")
		try:
			data = resp.json()
			return data.get("confirmationNumber") or data.get("cancellationId") or "cancelled"
		except Exception:
			return "cancelled"

	def track_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""GET /tracking/statuses?loadId=..."""
		fc = self.get_fcs(doc, settings_name)
		load_id = doc.get("shipment_id") or ""
		if not load_id:
			return {}

		with httpx.Client() as client:
			resp = client.get(
				self.url(fc, "/tracking/statuses"),
				params={"loadId": load_id},
				headers=self.headers(fc),
				timeout=30,
			)
		self.raise_for_status(resp, "track_shipment GET /tracking/statuses")
		return resp.json()

	def get_documents(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""GET /shipments/{loadId}/documents."""
		fc = self.get_fcs(doc, settings_name)
		load_id = doc.get("shipment_id") or ""
		if not load_id:
			return []

		with httpx.Client() as client:
			resp = client.get(
				self.url(fc, f"/shipments/{load_id}/documents"),
				headers=self.headers(fc),
				timeout=30,
			)
		self.raise_for_status(resp, "get_documents GET /documents")
		data = resp.json()
		raw_docs = data if isinstance(data, list) else data.get("documents") or []

		return [
			{
				"document_type": d.get("documentType") or d.get("type") or "BOL",
				"format": "PDF",
				"content": d.get("content") or d.get("base64") or "",
				"file_name": d.get("fileName") or f"{d.get('documentType','BOL')}.pdf",
			}
			for d in raw_docs
		]

	def book_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		raise NotImplementedError("Use schedule_ltl_pickup for Banyan booking.")

	def list_ltl_carriers(
		self,
		settings_name: str | None = None,
		create_transporters: bool = False,
		company: str | None = None,
		supplier: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		return []

	def get_carrier_id_for_supplier(
		self, supplier_name: str, settings_name: str | None = None, company: str | None = None
	) -> str | None:
		return None

	def get_package_type_options(
		self,
		carrier_id: str | None = None,
		settings_name: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		# Banyan static data: GET /staticdata/packagetypes
		# Returning common defaults until credentials are available
		return [
			{"value": "Pallets", "label": "Pallets"},
			{"value": "Skids", "label": "Skids"},
			{"value": "Cartons", "label": "Cartons"},
			{"value": "Crates", "label": "Crates"},
			{"value": "Drums", "label": "Drums"},
			{"value": "Bags", "label": "Bags"},
			{"value": "Rolls", "label": "Rolls"},
		]

	def get_carrier_service_levels(
		self, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		return []

	def get_accessorial_service_fields(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {"supported": list(BANYAN_ACCESSORIAL_CODES.keys()), "unsupported": []}

	def get_shipment_dimension_uoms(self) -> dict:
		return {
			"length_uom": ["Inch", "Centimeter", "Foot"],
			"weight_uom": ["Pound", "Kilogram"],
			"density_uom": [],
		}

	def validate_required_shipment_form_fields(
		self, doc: Shipment, settings_name: str | None = None
	) -> str | None:
		missing = []
		if not doc.get("pickup_address_name"):
			missing.append("Pickup Address")
		if not doc.get("delivery_address_name"):
			missing.append("Delivery Address")
		if not doc.get("pickup_date"):
			missing.append("Pickup Date")
		if not doc.get("shipment_delivery_note"):
			missing.append("Shipment Delivery Note items")
		if missing:
			return _("The following fields are required for Banyan quoting: {0}").format(", ".join(missing))
		return None

	def supports_quote_or_spot_quote(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {"supports_quote": True, "supports_spot_quote": False}
