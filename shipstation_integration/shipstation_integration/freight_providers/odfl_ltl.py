# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Old Dominion Freight Line (ODFL) LTL carrier integration.

Workflow
--------
1. ``fetch_ltl_offers`` / ``get_ltl_quotes``
   SOAP POST https://www.odfl.com/wsRate_v6/RateService
   Auth: inline in SOAP body (``odfl4MeUser`` / ``odfl4MePassword``).
   Saves one Shipment Quotation.
   ``quote_or_offer_id`` = ODFL ``referenceNumber`` (rate reference).

2. User accepts quotation.

3. ``schedule_ltl_pickup``
   a. POST /BOL/v3.1/eBOL/bol-external-per-standards → PRO number, BOL PDF
   b. POST /pickup/v3.0/create → pickup confirmation number
   Sets ``awb_number`` (PRO), ``pickup_id``, attaches BOL.

4. ``get_documents``  → GET  /document-retrieval-api/v1.0/getDocument
5. ``track_shipment`` → GET  /tracking/v2.0/shipment.track
6. ``cancel_shipment``→ DELETE /pickup/v3.0/cancel + DELETE /BOL/v3.1/eBOL/deleteBolPro

Authentication (REST endpoints)
--------------------------------
1. GET /auth/v1.0/token  with Basic auth (``client_id`` / ``client_secret`` from FCS)
   → returns a Bearer session token (1-hour TTL).
   Cached in ``frappe.cache`` keyed by FCS record name.

Note on Rate Estimate
---------------------
The Rate Estimate API remains SOAP through at least mid-2026. The SOAP body
contains the credentials inline (``odfl4MeUser`` / ``odfl4MePassword``) — these
are the same username and password used for the REST session token.

Country codes: ODFL uses 3-letter ISO (USA, CAN, MEX).
ODFL base URLs:
  Production: https://api.odfl.com
  QA:         https://apiq.odfl.com
"""
from __future__ import annotations

import base64
import time
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

import frappe
import httpx
from frappe import _
from frappe.utils import now
from frappe.utils.file_manager import save_file

from shipstation_integration.base_ltl import (
	BaseLTL,
	persist_shipment_ltl_fields,
	require_submitted_shipment_for_ltl,
)
from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.utils import get_shipment_company_for_ltl

if TYPE_CHECKING:
	from erpnext.stock.doctype.shipment.shipment import Shipment

TOKEN_REFRESH_BUFFER = 120  # ODFL tokens are 1 hour; refresh 2 min early
SOAP_RATE_URL = "https://www.odfl.com/wsRate_v6/RateService"

# ISO 3166-1 alpha-2 → alpha-3 mapping (ODFL-relevant subset)
ISO2_TO_ISO3: dict[str, str] = {
	"US": "USA",
	"CA": "CAN",
	"MX": "MEX",
}

# Accessorial code cross-reference
ODFL_ACCESSORIAL_CODES: dict[str, str] = {
	"additional_insurance_or_excess_value": "IND",
	"appointment_required_at_delivery": "CA",
	"construction_site_delivery": "CSD",
	"construction_site_pickup": "CSP",
	"hazardous_material": "HAZ",
	"inside_delivery": "IDC",
	"inside_pickup": "IPC",
	"lift_gate_required_at_delivery": "HYD",
	"lift_gate_required_at_pickup": "HYO",
	"limited_access_delivery": "LDC",
	"limited_access_pickup": "LPC",
	"notify_before_delivery": "ARN",
	"over_dimension_excessive_weight": "OVL",
	"protect_from_cold": "PFF",
	"residential_delivery": "RDC",
	"residential_pickup": "RPC",
	"sort_and_segregate": "SSC",
	"tradeshow_delivery": "EXD",
	"tradeshow_pickup": "EXO",
}

# SOAP rate service envelope template
RATE_SOAP_ENVELOPE = """\
<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:rate="http://www.odfl.com/ws/router/types/v4">
  <soapenv:Header/>
  <soapenv:Body>
    <rate:rateRequest>
      <rate:odfl4MeUser>{username}</rate:odfl4MeUser>
      <rate:odfl4MePassword>{password}</rate:odfl4MePassword>
      <rate:requestAction>RateEstimate</rate:requestAction>
      <rate:pickupDate>{pickup_date}</rate:pickupDate>
      <rate:tariffHeaderKeyString/>
      <rate:originCity>{origin_city}</rate:originCity>
      <rate:originState>{origin_state}</rate:originState>
      <rate:originZip>{origin_zip}</rate:originZip>
      <rate:originCountry>{origin_country}</rate:originCountry>
      <rate:destinationCity>{dest_city}</rate:destinationCity>
      <rate:destinationState>{dest_state}</rate:destinationState>
      <rate:destinationZip>{dest_zip}</rate:destinationZip>
      <rate:destinationCountry>{dest_country}</rate:destinationCountry>
      <rate:requestReferenceNumber>true</rate:requestReferenceNumber>
      <rate:billToAccountNbr>{bill_to_account}</rate:billToAccountNbr>
      <rate:paymentTerms>{payment_terms}</rate:paymentTerms>
      {commodity_xml}
      {accessorial_xml}
    </rate:rateRequest>
  </soapenv:Body>
</soapenv:Envelope>
"""

COMMODITY_TEMPLATE = """\
      <rate:commodityInfo>
        <rate:freightClass>{freight_class}</rate:freightClass>
        <rate:weight>{weight}</rate:weight>
        <rate:pieces>{pieces}</rate:pieces>
      </rate:commodityInfo>"""

ACCESSORIAL_TEMPLATE = """\
      <rate:accessorialList>
        <rate:accessorialCode>{code}</rate:accessorialCode>
      </rate:accessorialList>"""


class OdflLTL(BaseLTL):
	"""ODFL direct carrier LTL provider."""

	def __init__(self):
		self.provider = "ODFL"

	def get_fcs(self, doc: Shipment | None, settings_name: str | None):
		if settings_name and frappe.db.exists("Freight Carrier Settings", settings_name):
			return frappe.get_doc("Freight Carrier Settings", settings_name)
		co = get_shipment_company_for_ltl(doc)
		supplier = getattr(doc, "preferred_carrier", None)
		fc = get_freight_carrier_settings(co, supplier) if co and supplier else None
		if not fc:
			frappe.throw(
				_("No ODFL Freight Carrier Settings found for company {0} and supplier {1}.").format(
					co, supplier
				)
			)
		return fc

	def credentials(self, fc) -> tuple[str, str]:
		"""Return (username, password) from FCS client_id / client_secret."""
		username = fc.client_id or ""
		password = fc.get_password("client_secret") if hasattr(fc, "get_password") else ""
		if not username or not password:
			frappe.throw(
				_(
					"ODFL Freight Carrier Settings {0} requires client_id (username) and client_secret (password)."
				).format(fc.name)
			)
		return username, password

	def get_bearer_token(self, fc) -> str:
		"""Obtain a REST session token from /auth/v1.0/token (Basic auth)."""
		cache_key = f"odfl_token:{fc.name}"
		cached = frappe.cache.get_value(cache_key)
		if cached and cached.get("expires_at", 0) > time.time() + TOKEN_REFRESH_BUFFER:
			return cached["access_token"]

		username, password = self.credentials(fc)
		with httpx.Client() as client:
			resp = client.get(
				self.url(fc, "/auth/v1.0/token"),
				auth=(username, password),
				timeout=30,
			)
		self.raise_for_odfl_rest(resp, "token")
		payload = resp.json()
		token = payload.get("access_token") or payload.get("token") or ""
		expires_in = int(payload.get("expires_in", 3600))

		frappe.cache.set_value(
			cache_key,
			{"access_token": token, "expires_at": time.time() + expires_in},
			expires_in_sec=expires_in,
		)
		return token

	def rest_headers(self, fc) -> dict:
		return {
			"Authorization": f"Bearer {self.get_bearer_token(fc)}",
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

	def url(self, fc, path: str) -> str:
		base = (fc.base_url or "https://api.odfl.com").rstrip("/")
		return f"{base}/{path.lstrip('/')}"

	@staticmethod
	def is_qa_environment(fc) -> bool:
		return "apiq.odfl.com" in (fc.base_url or "")

	@staticmethod
	def parse_soap_fault(xml_text: str) -> str | None:
		"""Return SOAP faultstring text when the body is a fault envelope."""
		try:
			root = ET.fromstring(xml_text)
		except ET.ParseError:
			return None
		for el in root.iter():
			tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
			if tag == "faultstring" and el.text:
				return el.text.strip()
		return None

	def raise_for_odfl_soap(self, resp: httpx.Response, context: str = "") -> None:
		"""Raise when the SOAP body contains a fault or HTTP status is an error."""
		fault = self.parse_soap_fault(resp.text)
		if fault:
			label = f" ({context})" if context else ""
			frappe.log_error(title=f"ODFL SOAP fault{label}", message=fault)
			frappe.throw(
				_("ODFL rate API error{0}: {1}").format(label, fault),
				title=_("ODFL LTL Error"),
			)
		if resp.is_error:
			detail = resp.text
			label = f" ({context})" if context else ""
			frappe.log_error(
				title=f"ODFL SOAP HTTP {resp.status_code}{label}",
				message=detail,
			)
			frappe.throw(
				_("ODFL rate API HTTP error {0}{1}:\n{2}").format(resp.status_code, label, detail),
				title=_("ODFL LTL Error"),
			)

	def raise_for_odfl_rest(self, resp: httpx.Response, context: str = "") -> None:
		"""Raise with ODFL REST error body surfaced in the UI and error log."""
		if resp.is_error:
			try:
				body = resp.json()
			except Exception:
				body = resp.text
			detail = body if isinstance(body, str) else frappe.as_json(body, indent=2)
			label = f" ({context})" if context else ""
			frappe.log_error(
				title=f"ODFL REST {resp.status_code}{label}",
				message=detail,
			)
			frappe.throw(
				_("ODFL API error {0}{1}:\n{2}").format(resp.status_code, label, detail),
				title=_("ODFL LTL Error"),
			)

	@staticmethod
	def iso3_country(two_letter: str) -> str:
		return ISO2_TO_ISO3.get((two_letter or "").upper(), "USA")

	@staticmethod
	def odfl_address(info: dict, account: str = "") -> dict:
		"""Convert ShipstationLTL.get_address_and_contact_info output → ODFL address block."""
		addr = info["address"]
		contact = info["contact"]
		phone = (
			(contact["phone_number"] or "")
			.replace("-", "")
			.replace(" ", "")
			.replace("(", "")
			.replace(")", "")[:10]
		)
		block = {
			"address1": addr["address_line1"] or "",
			"address2": addr.get("address_line2") or "",
			"city": addr["city_locality"],
			"stateProvince": addr["state_province"],
			"postalCode": addr["postal_code"],
			"country": OdflLTL.iso3_country(addr["country_code"]),
			"name": addr.get("company_name") or "",
			"contact": {
				"name": contact["name"] or "",
				"phone": phone,
				"email": contact["email"] or "",
			},
		}
		if account:
			block["account"] = account
		return block

	def build_rate_soap(self, doc: Shipment, fc) -> str:
		"""Build the ODFL SOAP rate request XML."""
		username, password = self.credentials(fc)
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)

		o = origin_info["address"]
		d = dest_info["address"]

		packages = ltl.build_packages_from_sdn(doc)
		commodity_xml = "\n".join(
			COMMODITY_TEMPLATE.format(
				freight_class=str(p.get("freight_class", "50")),
				weight=int(p["weight"]["value"]),
				pieces=int(p.get("quantity", 1)),
			)
			for p in packages
		)

		accessorial_codes = [code for field, code in ODFL_ACCESSORIAL_CODES.items() if doc.get(field)]
		accessorial_xml = "\n".join(ACCESSORIAL_TEMPLATE.format(code=code) for code in accessorial_codes)

		billing_type = doc.get("billing_type") or "Shipper"
		payment_terms_map = {"Shipper": "PPD", "Consignee": "CC", "Third Party": "TP"}
		payment_terms = payment_terms_map.get(billing_type, "PPD")

		return RATE_SOAP_ENVELOPE.format(
			username=username,
			password=password,
			pickup_date=str(doc.get("pickup_date") or ""),
			origin_city=o["city_locality"],
			origin_state=o["state_province"],
			origin_zip=o["postal_code"],
			origin_country=self.iso3_country(o["country_code"]),
			dest_city=d["city_locality"],
			dest_state=d["state_province"],
			dest_zip=d["postal_code"],
			dest_country=self.iso3_country(d["country_code"]),
			bill_to_account=fc.account_number or "",
			payment_terms=payment_terms,
			commodity_xml=commodity_xml,
			accessorial_xml=accessorial_xml,
		)

	@staticmethod
	def parse_rate_response(xml_text: str) -> dict:
		"""Parse the SOAP rate response XML into a normalised dict."""
		try:
			root = ET.fromstring(xml_text)
		except ET.ParseError:
			return {}

		ns = {
			"soap": "http://schemas.xmlsoap.org/soap/envelope/",
			"rate": "http://www.odfl.com/ws/router/types/v4",
		}

		def find_text(el, tag):
			node = el.find(tag, ns)
			return node.text if node is not None else ""

		body = root.find(".//rate:rateResponse", ns) or root

		return {
			"referenceNumber": find_text(body, "rate:referenceNumber"),
			"netFreightCharge": find_text(body, "rate:netFreightCharge"),
			"grossFreightCharge": find_text(body, "rate:grossFreightCharge"),
			"fuelSurcharge": find_text(body, "rate:fuelSurchargeCharge"),
			"totalCharge": find_text(body, "rate:totalCharge"),
			"transitDays": find_text(body, "rate:transitDays"),
			"deliveryDate": find_text(body, "rate:deliveryDate"),
		}

	@staticmethod
	def odfl_charges_normalized(rate: dict) -> list[dict]:
		"""Charge rows in ShipEngine shape for save_selected_ltl_quotes / Shipment Quotation."""
		out: list[dict] = []
		for label, key in (
			("Net freight", "netFreightCharge"),
			("Gross freight", "grossFreightCharge"),
			("Fuel surcharge", "fuelSurcharge"),
		):
			raw = rate.get(key)
			try:
				amount = float(raw or 0)
			except (TypeError, ValueError):
				continue
			if amount:
				out.append(
					{
						"type": label,
						"amount": {"value": amount, "currency": "USD"},
						"description": "",
					}
				)
		return out

	def fetch_odfl_rate(self, doc: Shipment, fc) -> dict:
		"""POST SOAP rate service and return parsed rate dict."""
		soap_xml = self.build_rate_soap(doc, fc)
		with httpx.Client() as client:
			resp = client.post(
				SOAP_RATE_URL,
				content=soap_xml.encode("utf-8"),
				headers={
					"Content-Type": "text/xml; charset=utf-8",
					"SOAPAction": "RateEstimate",
				},
				timeout=60,
			)
		self.raise_for_odfl_soap(resp, "RateEstimate")
		return self.parse_rate_response(resp.text)

	def normalize_odfl_rate(self, doc: Shipment, rate: dict) -> dict:
		"""Map parsed SOAP rate to the standard offer dict for the quote dialog."""
		if not rate.get("totalCharge"):
			frappe.throw(
				_("ODFL returned no rate estimate for this shipment."),
				title=_("No ODFL quotes"),
			)

		try:
			grand_total = float(rate.get("totalCharge") or rate.get("netFreightCharge") or 0)
			transit_days = float(rate.get("transitDays") or 0) or None
		except (TypeError, ValueError):
			grand_total = 0.0
			transit_days = None

		ref = rate.get("referenceNumber") or ""
		return {
			"carrier_name": doc.preferred_carrier or "Old Dominion",
			"carrier_scac": "ODFL",
			"offer_id": ref,
			"transaction_id": ref,
			"service_level": "LTL",
			"total_price": grand_total,
			"currency": "USD",
			"transit_days": transit_days,
			"estimated_delivery_date": rate.get("deliveryDate") or None,
			"expiration_date": None,
			"is_spot_quote": False,
			"charges": self.odfl_charges_normalized(rate),
		}

	def save_odfl_quotation(self, doc: Shipment, rate: dict) -> float:
		"""Insert one Shipment Quotation from a parsed SOAP rate response."""
		offer = self.normalize_odfl_rate(doc, rate)
		sq = frappe.new_doc("Shipment Quotation")
		sq.shipment = doc.name
		sq.carrier = offer["carrier_name"]
		sq.carrier_scac = offer["carrier_scac"]
		sq.quote_or_offer_id = offer["offer_id"]
		sq.quote_or_offer_transaction_id = offer["transaction_id"]
		sq.service_level = offer["service_level"]
		sq.grand_total = offer["total_price"]
		sq.pickup_date = doc.get("pickup_date")
		if offer.get("transit_days") is not None:
			sq.estimated_delivery_days = float(offer["transit_days"])
		if offer.get("estimated_delivery_date"):
			sq.estimated_delivery_date = offer["estimated_delivery_date"]
		for charge in offer.get("charges") or []:
			amount_obj = charge.get("amount") or {}
			sq.append(
				"charges",
				{
					"type": charge.get("type") or "",
					"amount": float(amount_obj.get("value") or 0),
					"currency": amount_obj.get("currency") or "USD",
					"description": charge.get("description") or "",
				},
			)
		sq.insert(ignore_permissions=True)
		return float(offer["total_price"])

	def fetch_ltl_offers(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""Return ODFL SOAP rate as a normalized offer dict without saving anything."""
		require_submitted_shipment_for_ltl(doc)
		fc = self.get_fcs(doc, settings_name)
		rate = self.fetch_odfl_rate(doc, fc)
		return [self.normalize_odfl_rate(doc, rate)]

	def build_ebol_payload(self, doc: Shipment, fc, reference_number: str = "") -> dict:
		"""Build the REST eBOL creation payload."""
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)
		packages = ltl.build_packages_from_sdn(doc)

		billing_type = doc.get("billing_type") or "Shipper"
		payment_map = {"Shipper": "PPD", "Consignee": "CC", "Third Party": "TP"}
		account = fc.account_number or ""

		accessorial_codes = [code for field, code in ODFL_ACCESSORIAL_CODES.items() if doc.get(field)]

		line_items = []
		for pkg in packages:
			item = {
				"weight": int(pkg["weight"]["value"]),
				"weightUnit": "LBS",
				"classification": str(pkg.get("freight_class", "50")),
				"description": pkg.get("description") or "",
				"count": int(pkg.get("quantity", 1)),
				"length": int(pkg.get("dimensions", {}).get("length", 0)),
				"width": int(pkg.get("dimensions", {}).get("width", 0)),
				"height": int(pkg.get("dimensions", {}).get("height", 0)),
				"dimensionsUnit": "IN",
				"stackable": False,
				"hazardous": bool(doc.get("hazardous_material")),
			}
			if pkg.get("nmfc_code"):
				item["nmfc"] = pkg["nmfc_code"]
			line_items.append(item)

		shipper = self.odfl_address(origin_info, account=account)
		consignee = self.odfl_address(dest_info)

		payload: dict[str, Any] = {
			"shipper": shipper,
			"consignee": consignee,
			"payment": {
				"terms": payment_map.get(billing_type, "PPD"),
				"account": account,
			},
			"commodities": {
				"handlingUnits": [
					{
						"type": "PLT",
						"count": sum(p.get("quantity", 1) for p in packages),
						"lineItems": line_items,
					}
				]
			},
			"pickupDate": str(doc.get("pickup_date") or ""),
			"specialInstructions": doc.get("description_of_content") or "",
			"referenceNumbers": {
				"shipperRefNumber": doc.name,
			},
			"images": {
				"includeBol": True,
				"includeShippingLabels": True,
			},
		}
		if billing_type in ("Shipper", "Third Party"):
			payload["billTo"] = self.odfl_address(origin_info, account=account)
		if self.is_qa_environment(fc):
			payload["isTest"] = True
		if reference_number:
			payload["referenceNumbers"]["quoteId"] = reference_number
		if accessorial_codes:
			payload["accessorials"] = {"codes": accessorial_codes}

		return payload

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Call ODFL SOAP rate service and save one Shipment Quotation."""
		require_submitted_shipment_for_ltl(doc)
		fc = self.get_fcs(doc, settings_name)
		rate = self.fetch_odfl_rate(doc, fc)
		grand_total = self.save_odfl_quotation(doc, rate)
		return _("ODFL rate estimate saved as Shipment Quotation. Total: {0}").format(grand_total)

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Create eBOL (gets PRO) then schedule pickup."""
		require_submitted_shipment_for_ltl(doc)
		fc = self.get_fcs(doc, settings_name)

		accepted_sq_name = doc.accepted_quotation or frappe.db.get_value(
			"Shipment Quotation", {"shipment": doc.name, "docstatus": 1}, "name"
		)
		reference_number = ""
		if accepted_sq_name:
			reference_number = (
				frappe.db.get_value("Shipment Quotation", accepted_sq_name, "quote_or_offer_id") or ""
			)

		bol_payload = self.build_ebol_payload(doc, fc, reference_number=reference_number)
		with httpx.Client() as client:
			bol_resp = client.post(
				self.url(fc, "/BOL/v3.1/eBOL/bol-external-per-standards"),
				json=bol_payload,
				headers=self.rest_headers(fc),
				timeout=60,
			)
		self.raise_for_odfl_rest(bol_resp, "eBOL create")
		bol_data = bol_resp.json()

		pro_number = bol_data.get("proNumber") or bol_data.get("PRO") or ""
		bol_number = bol_data.get("bolNumber") or pro_number

		dt, dn = doc.doctype, doc.name
		persist_shipment_ltl_fields(
			dt,
			dn,
			{
				"carrier": doc.preferred_carrier or "Old Dominion",
				"carrier_service": "LTL",
				"awb_number": pro_number,
				"shipment_id": pro_number,
			},
		)

		docs_saved = False
		try:
			images = bol_data.get("images") or {}
			bol_b64 = images.get("bol") or bol_data.get("bolDocument") or ""
			if bol_b64:
				now_dt = now().split(".")[0]
				save_file(
					f"{doc.name}-BOL-{now_dt}.pdf",
					base64.b64decode(bol_b64),
					"Shipment",
					doc.name,
				)
				docs_saved = True
		except Exception:
			frappe.log_error(
				title="ODFL: Error attaching BOL",
				message=frappe.get_traceback(),
				reference_doctype="Shipment",
				reference_name=doc.name,
			)

		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		pickup_payload = {
			"pickupDate": str(doc.get("pickup_date") or ""),
			"readyTime": "08:00",
			"closeTime": "17:00",
			"pickupAddress": self.odfl_address(origin_info),
			"shipments": [{"proNumber": pro_number, "bolNumber": bol_number}],
			"specialInstructions": doc.get("description_of_content") or "",
		}
		with httpx.Client() as client:
			pickup_resp = client.post(
				self.url(fc, "/pickup/v3.0/create"),
				json=pickup_payload,
				headers=self.rest_headers(fc),
				timeout=30,
			)
		self.raise_for_odfl_rest(pickup_resp, "pickup create")
		pickup_data = pickup_resp.json()
		pickup_confirmation = (
			pickup_data.get("pickupConfirmationNumber") or pickup_data.get("confirmationNumber") or ""
		)
		if pickup_confirmation:
			persist_shipment_ltl_fields(dt, dn, {"pickup_id": pickup_confirmation})

		msg = _("ODFL eBOL created. PRO: {0}").format(pro_number)
		if pickup_confirmation:
			msg += _(" Pickup confirmed: {0}").format(pickup_confirmation)
		if docs_saved:
			msg += " " + _("BOL has been attached.")
		return msg

	def cancel_shipment(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Cancel pickup and delete eBOL."""
		fc = self.get_fcs(doc, settings_name)
		pro_number = doc.get("awb_number") or doc.get("shipment_id") or ""
		pickup_id = doc.get("pickup_id") or ""

		cancelled = []

		if pickup_id:
			try:
				with httpx.Client() as client:
					resp = client.delete(
						self.url(fc, f"/pickup/v3.0/cancel?pickupConfirmationNumber={pickup_id}"),
						headers=self.rest_headers(fc),
						timeout=30,
					)
				self.raise_for_odfl_rest(resp, "pickup cancel")
				cancelled.append(f"pickup {pickup_id}")
			except Exception:
				frappe.log_error(
					title="ODFL: Pickup cancel failed",
					message=frappe.get_traceback(),
					reference_doctype="Shipment",
					reference_name=doc.name,
				)

		if pro_number:
			try:
				with httpx.Client() as client:
					resp = client.delete(
						self.url(fc, f"/BOL/v3.1/eBOL/deleteBolPro?proNumber={pro_number}"),
						headers=self.rest_headers(fc),
						timeout=30,
					)
				self.raise_for_odfl_rest(resp, "eBOL delete")
				cancelled.append(f"eBOL {pro_number}")
			except Exception:
				frappe.log_error(
					title="ODFL: eBOL delete failed",
					message=frappe.get_traceback(),
					reference_doctype="Shipment",
					reference_name=doc.name,
				)

		return "Cancelled: " + ", ".join(cancelled) if cancelled else None

	def track_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""GET /tracking/v2.0/shipment.track by PRO number."""
		fc = self.get_fcs(doc, settings_name)
		pro = doc.get("awb_number") or doc.get("shipment_id") or ""
		if not pro:
			return {}

		with httpx.Client() as client:
			resp = client.get(
				self.url(fc, "/tracking/v2.0/shipment.track"),
				params={"proNumber": pro},
				headers=self.rest_headers(fc),
				timeout=30,
			)
		self.raise_for_odfl_rest(resp, "tracking")
		return resp.json()

	def get_documents(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""GET /document-retrieval-api/v1.0/getDocument by PRO number."""
		fc = self.get_fcs(doc, settings_name)
		pro = doc.get("awb_number") or doc.get("shipment_id") or ""
		if not pro:
			return []

		with httpx.Client() as client:
			resp = client.get(
				self.url(fc, "/document-retrieval-api/v1.0/getDocument"),
				params={"proNumber": pro, "documentType": "BOL"},
				headers=self.rest_headers(fc),
				timeout=30,
			)
		self.raise_for_odfl_rest(resp, "documents")
		data = resp.json()
		raw_docs = data if isinstance(data, list) else data.get("documents") or [data]

		return [
			{
				"document_type": d.get("documentType") or "BOL",
				"format": "PDF",
				"content": d.get("content") or d.get("base64Document") or "",
				"file_name": d.get("fileName") or f"ODFL-{pro}-{d.get('documentType','BOL')}.pdf",
			}
			for d in raw_docs
			if d.get("content") or d.get("base64Document")
		]

	def book_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		raise NotImplementedError("Use schedule_ltl_pickup for ODFL booking.")

	def list_ltl_carriers(
		self,
		settings_name: str | None = None,
		create_transporters: bool = False,
		company: str | None = None,
		supplier: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		return [
			{
				"name": "Old Dominion Freight Line",
				"carrier_id": "ODFL",
				"carrier_code": "ODFL",
				"options": [],
				"services": [],
				"packages": [],
			}
		]

	def get_carrier_id_for_supplier(
		self, supplier_name: str, settings_name: str | None = None, company: str | None = None
	) -> str | None:
		return "ODFL"

	def get_package_type_options(
		self,
		carrier_id: str | None = None,
		settings_name: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		return [
			{"value": "PLT", "label": "Pallet"},
			{"value": "SKD", "label": "Skid"},
			{"value": "CTN", "label": "Carton"},
			{"value": "CRT", "label": "Crate"},
			{"value": "DRM", "label": "Drum"},
			{"value": "BAG", "label": "Bag"},
			{"value": "RLL", "label": "Roll"},
			{"value": "PCE", "label": "Piece"},
		]

	def get_carrier_service_levels(
		self, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		return [
			{"value": "LTL", "label": "Standard LTL"},
			{"value": "GTD", "label": "Guaranteed Service"},
			{"value": "GTD1200", "label": "Guaranteed by Noon"},
			{"value": "GTD1700", "label": "Guaranteed by 5 PM"},
			{"value": "EXPED", "label": "OD Expedited LTL"},
		]

	def get_accessorial_service_fields(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {"supported": list(ODFL_ACCESSORIAL_CODES.keys()), "unsupported": []}

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
		try:
			fc = self.get_fcs(doc, settings_name)
			if not (fc.account_number or "").strip():
				missing.append("Account Number (Freight Carrier Settings)")
		except Exception:
			pass
		if missing:
			return _("The following fields are required for ODFL quoting: {0}").format(", ".join(missing))
		return None

	def supports_quote_or_spot_quote(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {"supports_quote": True, "supports_spot_quote": False}
