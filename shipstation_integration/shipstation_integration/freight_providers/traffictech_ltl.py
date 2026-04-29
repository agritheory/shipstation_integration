# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""TrafficTech LTL Quoting API integration (LTLRate).

Workflow
--------
1. ``get_ltl_quotes`` / ``fetch_ltl_offers``
   POST ``{base_url}?message-type=LTLRate`` with header ``subscription-key``.
   Saves one Shipment Quotation per quote in ``quotes[]``.
   ``quote_or_offer_id``             = ``quoteId``
   ``quote_or_offer_transaction_id`` = ``loadId`` (from the response)

2. User accepts one quotation (submits SQ).

3. ``schedule_ltl_pickup`` / ``book_shipment`` / ``cancel_shipment``
   Not implemented — TrafficTech documents booking separately from the rate API.

Authentication
--------------
- ``subscription-key`` header: Freight Carrier Settings **LTL API Key** (password field).
- JSON body: ``customerId`` from **Account Number**; portal login fields from
  **Client ID** (email) and **Client Secret** (password), per TrafficTech spec.
"""
from __future__ import annotations

import json
from datetime import datetime, time
from typing import TYPE_CHECKING, Any

import frappe
import httpx
from frappe import _
from frappe.utils import flt, getdate
from shipstation_integration.base_ltl import BaseLTL
from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.rates import get_state_code
from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.utils import get_shipment_company_for_ltl

if TYPE_CHECKING:
	from erpnext.stock.doctype.shipment.shipment import Shipment

# ShipEngine-style plural units from ``build_packages_from_sdn`` → TrafficTech uses IN / LBS.
WEIGHT_UNIT_TO_LB: dict[str, float] = {
	"pounds": 1.0,
	"kilograms": 2.20462,
	"ounces": 0.0625,
	"grams": 0.00220462,
}
DIM_UNIT_TO_INCH: dict[str, float] = {
	"inches": 1.0,
	"centimeters": 0.393701,
	"feet": 12.0,
}

# Map ShipStation / internal package codes → (packageType, packageTypeDescription).
TT_PACKAGE_BY_CODE: dict[str, tuple[str, str]] = {
	"plt": ("PLTS", "Pallets"),
	"plts": ("PLTS", "Pallets"),
	"nsplts": ("NSPLTS", "Non Stack. Pallets"),
	"skid": ("PLTS", "Pallets"),
	"box": ("BOXS", "Boxes"),
	"boxs": ("BOXS", "Boxes"),
	"ctn": ("CRTN", "Cartons"),
	"crtn": ("CRTN", "Cartons"),
	"crt": ("CRTS", "Crates"),
	"crts": ("CRTS", "Crates"),
	"bag": ("BAGS", "Bags"),
	"bags": ("BAGS", "Bags"),
	"drm": ("DRUM", "Drums"),
	"drum": ("DRUM", "Drums"),
	"bale": ("BALE", "Bales"),
	"roll": ("ROLL", "Rolls"),
}

# Shipment field → TrafficTech ``accessorials`` key (PDF Section 4.2)
TRAFFICTECH_ACCESSORIAL_FIELDS: dict[str, str] = {
	"residential_pickup": "isResidentialPickup",
	"inside_pickup": "isInsidePickup",
	"lift_gate_required_at_pickup": "isLiftgatePickup",
	"appointment_required_at_delivery": "appointmentRequired",
	"residential_delivery": "isResidentialDelivery",
	"inside_delivery": "isInsideDelivery",
	"lift_gate_required_at_delivery": "isLiftgateDelivery",
	"notify_before_delivery": "deliverNotification",
	"sort_and_segregate": "sortSegregate",
}


class TrafficTechLTL(BaseLTL):
	"""TrafficTech LTL rate quote provider (LTLRate)."""

	def __init__(self):
		self.provider = "TrafficTech"

	def get_fcs(self, doc: Shipment | None, settings_name: str | None):
		if settings_name and frappe.db.exists("Freight Carrier Settings", settings_name):
			return frappe.get_doc("Freight Carrier Settings", settings_name)
		co = get_shipment_company_for_ltl(doc)
		supplier = getattr(doc, "preferred_carrier", None)
		fc = get_freight_carrier_settings(co, supplier) if co and supplier else None
		if not fc:
			frappe.throw(
				_("No TrafficTech Freight Carrier Settings found for company {0} and supplier {1}.").format(
					co, supplier
				)
			)
		return fc

	def raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
		if resp.is_error:
			try:
				body = resp.json()
			except Exception:
				body = resp.text
			detail = body if isinstance(body, str) else frappe.as_json(body, indent=2)
			label = f" ({context})" if context else ""
			frappe.log_error(
				title=f"TrafficTech LTL {resp.status_code}{label}",
				message=detail,
			)
			frappe.throw(
				_("TrafficTech API error {0}{1}:\n{2}").format(resp.status_code, label, detail),
				title=_("TrafficTech LTL Error"),
			)

	def subscription_key(self, fc) -> str:
		key = fc.get_password("ltl_api_key", raise_exception=False) or ""
		if not key:
			frappe.throw(
				_("LTL API Key (subscription-key) is not set on Freight Carrier Settings <b>{0}</b>.").format(
					fc.name
				),
				title=_("Missing TrafficTech API Key"),
			)
		return key

	def parse_customer_id(self, fc) -> int:
		raw = (fc.account_number or "").strip()
		if not raw:
			frappe.throw(
				_(
					"Account Number (TrafficTech customer ID) is not set on Freight Carrier Settings <b>{0}</b>."
				).format(fc.name),
				title=_("Missing TrafficTech Customer ID"),
			)
			raise AssertionError("unreachable")
		try:
			return int(raw)
		except ValueError:
			frappe.throw(
				_("TrafficTech customer ID (Account Number) must be an integer, not <b>{0}</b>.").format(raw),
				title=_("Invalid TrafficTech Customer ID"),
			)
			raise AssertionError("unreachable")

	def portal_credentials(self, fc) -> tuple[str, str]:
		email = fc.get_password("client_id", raise_exception=False) or ""
		password = fc.get_password("client_secret", raise_exception=False) or ""
		if not email or not password:
			frappe.throw(
				_(
					"TrafficTech portal credentials are missing on Freight Carrier Settings <b>{0}</b>. "
					"Set Client ID (portal email) and Client Secret (portal password)."
				).format(fc.name),
				title=_("Missing TrafficTech Portal Credentials"),
			)
		return email, password

	def rate_url(self, fc) -> str:
		base = (fc.base_url or "").strip().rstrip("/")
		if not base:
			frappe.throw(
				_("Base URL is not set on Freight Carrier Settings <b>{0}</b>.").format(fc.name),
				title=_("Missing TrafficTech Base URL"),
			)
		lower = base.lower()
		if "message-type=" in lower:
			return base
		return f"{base}{'&' if '?' in base else '?'}message-type=LTLRate"

	def format_time_hhmm(self, val: Any, default: str) -> str:
		if val is None or val == "":
			return default
		if isinstance(val, time):
			return val.strftime("%H:%M")
		if isinstance(val, datetime):
			return val.strftime("%H:%M")
		s = str(val).strip()
		if not s:
			return default
		# "09:00:00" → "09:00"
		return s[:5] if len(s) >= 5 and s[2] == ":" else s

	def format_pickup_date(self, doc: Shipment) -> str:
		pd = doc.get("pickup_date")
		if not pd:
			frappe.throw(_("Pickup Date is required for TrafficTech LTL quotes."))
		d = getdate(pd)
		return d.strftime("%b %d, %Y")

	def declared_value(self, doc: Shipment) -> int:
		# Mandatory in TT spec; PDF samples use 500 when unspecified.
		v = int(flt(doc.get("value_of_goods")) or 0)
		return v if v > 0 else 500

	@staticmethod
	def tt_location_block(
		info: dict,
		doc: Shipment,
		open_from: str,
		open_to: str,
	) -> dict:
		addr = info["address"]
		contact = info["contact"]
		cc = (addr.get("country_code") or "").upper()
		state = addr.get("state_province") or ""
		if cc == "US":
			state = get_state_code(state, "US") or state
		name_parts = (contact.get("name") or "").strip().split(" ", 1)
		instructions = (doc.get("description_of_content") or "")[:500]
		return {
			"id": 0,
			"companyName": (addr.get("company_name") or "")[:200],
			"address1": addr.get("address_line1") or "",
			"address2": addr.get("address_line2") or None,
			"phone": contact.get("phone_number") or "",
			"zip": (addr.get("postal_code") or "").strip(),
			"state": state,
			"isOpen24Hrs": False,
			"isDefaultOrigin": False,
			"isDefaultDestination": False,
			"openFrom": open_from,
			"openTo": open_to,
			"dockLimitedAccessType": "",
			"defaultInstructions": instructions,
			"shippingReference": None,
			"contact": {
				"name": None,
				"firstName": name_parts[0] if name_parts else "",
				"lastName": name_parts[1] if len(name_parts) > 1 else "",
				"jobDescription": None,
				"phone": contact.get("phone_number") or "",
				"extension": "",
				"mobile": None,
				"fax": None,
				"email": contact.get("email") or "",
			},
		}

	def build_accessorials(self, doc: Shipment) -> dict[str, bool]:
		out: dict[str, bool] = {}
		for ship_field, tt_key in TRAFFICTECH_ACCESSORIAL_FIELDS.items():
			out[tt_key] = bool(doc.get(ship_field))
		return out

	def package_type_for_tt(self, doc: Shipment, pkg: dict) -> tuple[str, str]:
		code = (pkg.get("code") or doc.get("package_type_code") or "").strip().lower()
		if code in TT_PACKAGE_BY_CODE:
			return TT_PACKAGE_BY_CODE[code]
		return ("PLTS", "Pallets")

	def build_freight_items(self, doc: Shipment, packages: list[dict]) -> list[dict]:
		items: list[dict] = []
		if doc.get("hazardous_material"):
			frappe.throw(
				_("Hazardous material shipments are not supported for TrafficTech LTL quoting."),
				title=_("TrafficTech LTL"),
			)
		for pkg in packages:
			wu = (pkg.get("weight") or {}).get("unit") or "pounds"
			du = (pkg.get("dimensions") or {}).get("unit") or "inches"
			w_fac = WEIGHT_UNIT_TO_LB.get(str(wu).lower(), 1.0)
			d_fac = DIM_UNIT_TO_INCH.get(str(du).lower(), 1.0)
			weight_lb = int(round(flt((pkg.get("weight") or {}).get("value")) * w_fac)) or 1
			dims = pkg.get("dimensions") or {}
			length = int(round(flt(dims.get("length")) * d_fac)) or 1
			width = int(round(flt(dims.get("width")) * d_fac)) or 1
			height = int(round(flt(dims.get("height")) * d_fac)) or 1
			freight_class = pkg.get("freight_class")
			cls = str(freight_class) if freight_class is not None else "50"
			desc = (pkg.get("description") or doc.get("description_of_content") or "Freight")[:200]
			pkg_type, pkg_desc = self.package_type_for_tt(doc, pkg)
			item: dict[str, Any] = {
				"class": cls,
				"description": desc,
				"height": height,
				"isHazmat": False,
				"length": length,
				"nmfc": str(pkg.get("nmfc_code") or ""),
				"packageType": pkg_type,
				"packageTypeDescription": pkg_desc,
				"quantity": int(pkg.get("quantity") or 1) or 1,
				"sizeUnit": "IN",
				"weight": weight_lb,
				"weightUnit": "LBS",
				"width": width,
			}
			items.append(item)
		return items

	def build_rate_payload(self, doc: Shipment, fc) -> dict:
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)
		pickup_open = self.format_time_hhmm(doc.get("pickup_from"), "08:00")
		pickup_close = self.format_time_hhmm(doc.get("pickup_to"), "17:00")
		shipper = self.tt_location_block(origin_info, doc, pickup_open, pickup_close)
		# Consignee: use same business hours defaults if not modeled separately
		consignee = self.tt_location_block(dest_info, doc, "08:00", "17:00")
		packages = ltl.build_packages_from_sdn(doc)
		customer_id = self.parse_customer_id(fc)
		portal_email, portal_password = self.portal_credentials(fc)
		o_cc = (origin_info["address"].get("country_code") or "").upper()
		d_cc = (dest_info["address"].get("country_code") or "").upper()
		currency = "CAD" if (o_cc == "CA" or d_cc == "CA") else "USD"

		payload: dict[str, Any] = {
			"accessorials": self.build_accessorials(doc),
			"clearingFor": "",
			"clearingForTypeId": None,
			"customBrokerId": None,
			"customerId": customer_id,
			"customerBillToId": None,
			"shipmentReferences": [],
			"customerContactEmail": portal_email,
			"customerContactEmailPassword": portal_password,
			"currency": currency,
			"shipper": shipper,
			"consignee": consignee,
			"declaredValue": self.declared_value(doc),
			"freightItems": self.build_freight_items(doc, packages),
			"loadId": None,
			"pickupDate": self.format_pickup_date(doc),
		}
		return payload

	def post_ltl_rate(self, doc: Shipment, fc) -> dict:
		url = self.rate_url(fc)
		headers = {
			"Content-Type": "application/json",
			"Accept": "application/json",
			"subscription-key": self.subscription_key(fc),
		}
		body = self.build_rate_payload(doc, fc)
		with httpx.Client() as client:
			resp = client.post(url, headers=headers, json=body, timeout=120)
		self.raise_for_status(resp, "POST LTLRate")
		try:
			return resp.json()
		except json.JSONDecodeError:
			frappe.throw(
				_("TrafficTech returned a non-JSON response."),
				title=_("TrafficTech LTL Error"),
			)
			raise AssertionError("unreachable")

	def parse_rate_response(self, data: dict) -> tuple[str, list[dict]]:
		status = (data.get("status") or "").strip().upper()
		if status != "OK":
			err = data.get("error") or data.get("message") or _("Unknown error")
			frappe.throw(
				_("TrafficTech rate request failed: {0}").format(err),
				title=_("TrafficTech LTL Error"),
			)
		load_id = str(data.get("loadId") or "")
		quotes = data.get("quotes") or []
		if not isinstance(quotes, list):
			quotes = []
		return load_id, [q for q in quotes if isinstance(q, dict)]

	def fetch_traffictech_quotes(self, doc: Shipment, fc) -> tuple[str, list[dict]]:
		data = self.post_ltl_rate(doc, fc)
		return self.parse_rate_response(data)

	@staticmethod
	def quote_charges_normalized(quote: dict, currency: str) -> list[dict]:
		"""Build ShipEngine-shaped charge dicts from optional TrafficTech breakdown."""
		out: list[dict] = []
		raw = quote.get("carrierCharges") or quote.get("charges") or quote.get("chargeDetails")
		if not isinstance(raw, list):
			return out
		for row in raw:
			if not isinstance(row, dict):
				continue
			label = row.get("description") or row.get("chargeType") or row.get("type") or "Charge"
			amt = flt(row.get("amount") or row.get("charge") or row.get("value"))
			out.append(
				{
					"type": str(label),
					"amount": {"value": amt, "currency": currency},
					"description": str(row.get("description") or ""),
				}
			)
		total = flt(quote.get("customerPrice"))
		if total and not any(str(c.get("type", "")).lower() == "total" for c in out):
			out.append(
				{"type": "total", "amount": {"value": total, "currency": currency}, "description": ""}
			)
		return out

	@staticmethod
	def append_quotation_charges_normalized(sq: Any, charges_normalized: list[dict]) -> None:
		for raw_charge in charges_normalized or []:
			if not isinstance(raw_charge, dict):
				continue
			charge: dict[str, Any] = raw_charge
			amount_obj = charge.get("amount")
			amount_inner = amount_obj if isinstance(amount_obj, dict) else {}
			c_type = charge.get("type", "N/A").title()
			amount = float(amount_inner.get("value", 0))
			if c_type.lower() == "discount":
				amount *= -1
			if c_type.lower() == "total":
				continue
			sq.append(
				"charges",
				{
					"type": c_type,
					"amount": amount,
					"currency": amount_inner.get("currency") or "USD",
					"description": charge.get("description") or "",
				},
			)

	def normalize_tt_quote(self, q: dict, load_id: str) -> dict[str, Any]:
		scac = (q.get("scac") or "").strip()
		carrier_name = (q.get("carrierName") or "").strip() or "Carrier"
		quote_id = q.get("quoteId")
		offer_id = str(quote_id) if quote_id is not None else ""
		curr = (q.get("currencyCode") or "USD").strip() or "USD"
		total = flt(q.get("customerPrice"))
		transit = q.get("transitTime")
		service = (q.get("transitTimeDescription") or q.get("quoteNumber") or "").strip()
		charges = self.quote_charges_normalized(q, curr)
		return {
			"carrier_name": carrier_name,
			"carrier_scac": scac,
			"offer_id": offer_id,
			"transaction_id": load_id,
			"service_level": service,
			"total_price": float(total),
			"currency": curr,
			"transit_days": int(transit) if transit is not None else None,
			"estimated_delivery_date": None,
			"expiration_date": None,
			"is_spot_quote": False,
			"charges": charges,
		}

	def fetch_ltl_offers(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		fc = self.get_fcs(doc, settings_name)
		load_id, quotes = self.fetch_traffictech_quotes(doc, fc)
		return [self.normalize_tt_quote(q, load_id) for q in quotes]

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		fc = self.get_fcs(doc, settings_name)
		load_id, quotes = self.fetch_traffictech_quotes(doc, fc)

		if not quotes:
			frappe.msgprint(_("TrafficTech returned no carrier quotes for this shipment."))
			return None

		saved = 0
		for q in quotes:
			n = self.normalize_tt_quote(q, load_id)
			scac = n["carrier_scac"]
			supplier_name = (
				frappe.db.get_value("Supplier", {"ltl_carrier_scac": scac, "is_transporter": 1}, "name")
				if scac
				else None
			)
			sq = frappe.new_doc("Shipment Quotation")
			sq.shipment = doc.name
			sq.carrier = supplier_name or n["carrier_name"]
			sq.carrier_scac = scac
			sq.quote_or_offer_id = n["offer_id"]
			sq.quote_or_offer_transaction_id = load_id
			sq.service_level = n["service_level"]
			sq.grand_total = n["total_price"]
			sq.pickup_date = doc.get("pickup_date")
			if n["transit_days"] is not None:
				sq.estimated_delivery_days = float(n["transit_days"])
			self.append_quotation_charges_normalized(sq, n["charges"])
			sq.insert(ignore_permissions=True)
			saved += 1

		return _("{0} TrafficTech carrier quote(s) saved as Shipment Quotation(s).").format(saved)

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		frappe.throw(
			_(
				"TrafficTech shipment booking is not available in this integration yet. "
				"The published LTL Quoting API covers rate requests (LTLRate) only. "
				"Contact TrafficTech when booking endpoints are enabled for your account."
			),
			title=_("TrafficTech booking unavailable"),
		)
		raise AssertionError("unreachable")

	def book_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		frappe.throw(
			_(
				"TrafficTech shipment booking is not available in this integration yet. "
				"The published LTL Quoting API covers rate requests (LTLRate) only. "
				"Contact TrafficTech when booking endpoints are enabled for your account."
			),
			title=_("TrafficTech booking unavailable"),
		)
		raise AssertionError("unreachable")

	def cancel_shipment(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		frappe.throw(
			_(
				"TrafficTech shipment cancellation is not available in this integration yet. "
				"Contact TrafficTech when cancellation is exposed for your account."
			),
			title=_("TrafficTech cancellation unavailable"),
		)
		raise AssertionError("unreachable")

	def track_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {}

	def get_documents(self, doc, settings_name: str | None = None) -> list[dict]:
		return []

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
		# TrafficTech Appendix B (subset of common codes)
		return [
			{"value": "PLTS", "label": "Pallets (PLTS)"},
			{"value": "NSPLTS", "label": "Non-Stackable Pallets (NSPLTS)"},
			{"value": "BOXS", "label": "Boxes (BOXS)"},
			{"value": "CRTN", "label": "Cartons (CRTN)"},
			{"value": "CRTS", "label": "Crates (CRTS)"},
			{"value": "BAGS", "label": "Bags (BAGS)"},
			{"value": "DRUM", "label": "Drums (DRUM)"},
			{"value": "BALE", "label": "Bales (BALE)"},
			{"value": "ROLL", "label": "Rolls (ROLL)"},
			{"value": "COIL", "label": "Coils (COIL)"},
		]

	def get_carrier_service_levels(
		self, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		return []

	def get_accessorial_service_fields(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {"supported": list(TRAFFICTECH_ACCESSORIAL_FIELDS.keys()), "unsupported": []}

	def get_shipment_dimension_uoms(self) -> dict:
		return {
			"length_uom": ["Inch", "Centimeter", "Foot"],
			"weight_uom": ["Pound", "Kilogram", "Ounce", "Gram"],
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
			return _("The following fields are required for TrafficTech LTL quoting: {0}").format(
				", ".join(missing)
			)
		return None

	def supports_quote_or_spot_quote(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {"supports_quote": True, "supports_spot_quote": False}
