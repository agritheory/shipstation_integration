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
Token endpoint: ``https://ws.integration.banyantechnology.com/api/v3/auth/token``
(or the URL stored in ``app.token_uri``).

Credentials can alternatively be stored as a Bearer token directly in
``Freight Carrier Settings.ltl_api_key`` (for static API keys).
"""
from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING

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

_TOKEN_REFRESH_BUFFER = 60

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

	# ------------------------------------------------------------------
	# Auth / request helpers
	# ------------------------------------------------------------------

	def _get_fcs(self, doc: Shipment | None, settings_name: str | None):
		if settings_name:
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

	def _get_token(self, fc) -> str:
		"""Return a bearer token via client_credentials grant.

		Priority:
		1. Static API key stored in ``ltl_api_key`` — returned as-is.
		2. ``client_id`` / ``client_secret`` on FCS — client_credentials grant to
		   ``/auth/connect/token``.
		3. Connected App — uses its client_id/client_secret.
		"""
		static_key = (
			fc.get_password("ltl_api_key") if hasattr(fc, "get_password") else (fc.ltl_api_key or "")
		)
		if static_key:
			return static_key

		cache_key = f"banyan_token:{fc.name}"
		cached = frappe.cache.get_value(cache_key)
		if cached and cached.get("expires_at", 0) > time.time() + _TOKEN_REFRESH_BUFFER:
			return cached["access_token"]

		token_url = self._url(fc, "/auth/connect/token")

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
			resp.raise_for_status()

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
			resp.raise_for_status()

		payload = resp.json()
		access_token = payload.get("access_token") or payload.get("token")
		expires_in = int(payload.get("expires_in", 3600))
		frappe.cache.set_value(
			cache_key,
			{"access_token": access_token, "expires_at": time.time() + expires_in},
			expires_in_sec=expires_in,
		)
		return access_token

	def _headers(self, fc) -> dict:
		return {
			"Authorization": f"Bearer {self._get_token(fc)}",
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

	def _url(self, fc, path: str) -> str:
		base = (fc.base_url or "https://ws.integration.banyantechnology.com/api/v3").rstrip("/")
		return f"{base}/{path.lstrip('/')}"

	# ------------------------------------------------------------------
	# Address helpers
	# ------------------------------------------------------------------

	@staticmethod
	def _banyan_location(info: dict, is_shipper: bool = True) -> dict:
		"""Convert ShipstationLTL.get_address_and_contact_info output → Banyan location block."""
		addr = info["address"]
		contact = info["contact"]
		return {
			"companyName": addr.get("company_name") or "",
			"address": {
				"address1": addr["address_line1"] or "",
				"address2": addr.get("address_line2") or "",
				"city": addr["city_locality"],
				"stateOrProvince": addr["state_province"],
				"zipCode": addr["postal_code"],
				"country": "United States",  # Banyan expects full country name
			},
			"contactPerson": {
				"firstName": (contact["name"] or "").split(" ")[0],
				"lastName": " ".join((contact["name"] or "").split(" ")[1:]) or "",
			},
			"contactMethods": {
				"phoneNumber": contact["phone_number"],
				"email": contact["email"],
			},
		}

	# ------------------------------------------------------------------
	# Payload builders
	# ------------------------------------------------------------------

	def _build_handling_units(self, doc: Shipment) -> list[dict]:
		"""Build Banyan handlingUnits[] from Shipment parcel groups."""
		ltl = ShipstationLTL()
		packages = ltl.build_packages_from_sdn(doc)
		units = []
		for pkg in packages:
			dims = pkg.get("dimensions", {})
			unit_dims = {
				"length": dims.get("length", 0),
				"width": dims.get("width", 0),
				"height": dims.get("height", 0),
				"unitOfMeasurement": "IN",
			}
			product = {
				"description": pkg.get("description") or "",
				"class": str(pkg.get("freight_class", "50")),
				"weight": pkg["weight"]["value"],
				"weightUnitOfMeasurement": "LBS",
				"dimensions": unit_dims,
				"quantity": int(pkg.get("quantity", 1)),
			}
			if pkg.get("nmfc_code"):
				product["nmfc"] = pkg["nmfc_code"]

			units.append(
				{
					"packageType": pkg.get("code") or "Pallets",
					"quantity": int(pkg.get("quantity", 1)),
					"products": [product],
				}
			)
		return units

	def _build_accessorial_list(self, doc: Shipment) -> list[str]:
		return [code for field, code in BANYAN_ACCESSORIAL_CODES.items() if doc.get(field)]

	def _build_shipment_payload(self, doc: Shipment, fc) -> dict:
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)

		# Billing
		billing_type = doc.get("billing_type") or "Shipper"
		banyan_pay_type_map = {
			"Shipper": "Prepaid",
			"Consignee": "Collect",
			"Third Party": "ThirdParty",
		}

		return {
			"shipperLocation": self._banyan_location(origin_info, is_shipper=True),
			"consigneeLocation": self._banyan_location(dest_info, is_shipper=False),
			"shipType": banyan_pay_type_map.get(billing_type, "Prepaid"),
			"pickupDate": str(doc.get("pickup_date") or ""),
			"handlingUnits": self._build_handling_units(doc),
			"accessorials": self._build_accessorial_list(doc),
			"referenceNumber": doc.name,
			"clientRefNumber": fc.account_number or "",
			# Rate retrieval mode: synchronous
			"waitForRates": True,
			"shouldRunRates": True,
		}

	# ------------------------------------------------------------------
	# BaseLTL interface
	# ------------------------------------------------------------------

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""POST /shipments and save one Shipment Quotation per quote returned."""
		fc = self._get_fcs(doc, settings_name)
		payload = self._build_shipment_payload(doc, fc)

		with httpx.Client() as client:
			resp = client.post(
				self._url(fc, "/shipments"),
				json=payload,
				headers=self._headers(fc),
				timeout=120,  # waitForRates can be slow
			)
		resp.raise_for_status()
		data = resp.json()

		load_id = data.get("loadId") or data.get("id") or ""
		quotes = data.get("quotes") or []

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

		frappe.db.commit()
		return _("{0} Banyan carrier quote(s) saved as Shipment Quotation(s).").format(saved)

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Book the accepted quote via POST /shipments/{loadId}/book."""
		fc = self._get_fcs(doc, settings_name)

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
				self._url(fc, f"/shipments/{load_id}/book"),
				json={"quoteId": int(quote_id)},
				headers=self._headers(fc),
				timeout=60,
			)
		resp.raise_for_status()
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
		fc = self._get_fcs(doc, settings_name)
		load_id = doc.get("shipment_id") or ""
		if not load_id:
			frappe.throw(_("No Banyan loadId (shipment_id) found on this Shipment."))

		with httpx.Client() as client:
			resp = client.post(
				self._url(fc, f"/shipments/{load_id}/cancel"),
				json={},
				headers=self._headers(fc),
				timeout=30,
			)
		resp.raise_for_status()
		try:
			data = resp.json()
			return data.get("confirmationNumber") or data.get("cancellationId") or "cancelled"
		except Exception:
			return "cancelled"

	def track_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""GET /tracking/statuses?loadId=..."""
		fc = self._get_fcs(doc, settings_name)
		load_id = doc.get("shipment_id") or ""
		if not load_id:
			return {}

		with httpx.Client() as client:
			resp = client.get(
				self._url(fc, "/tracking/statuses"),
				params={"loadId": load_id},
				headers=self._headers(fc),
				timeout=30,
			)
		resp.raise_for_status()
		return resp.json()

	def get_documents(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""GET /shipments/{loadId}/documents."""
		fc = self._get_fcs(doc, settings_name)
		load_id = doc.get("shipment_id") or ""
		if not load_id:
			return []

		with httpx.Client() as client:
			resp = client.get(
				self._url(fc, f"/shipments/{load_id}/documents"),
				headers=self._headers(fc),
				timeout=30,
			)
		resp.raise_for_status()
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

	# ------------------------------------------------------------------
	# Remaining BaseLTL methods
	# ------------------------------------------------------------------

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
