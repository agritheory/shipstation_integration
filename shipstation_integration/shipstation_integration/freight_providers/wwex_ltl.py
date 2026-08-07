# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""WWEX (Worldwide Express / SpeedShip) LTL carrier integration.

Workflow
--------
1. ``get_ltl_quotes``  → POST /svc/shopFlow  (productType: "LTL")
   Saves one Shipment Quotation per carrier offer returned.
   ``quote_or_offer_id``         = offer's ``shipmentOfferId``
   ``quote_or_offer_transaction_id`` = ``shipmentProductTransactionId``

2. User accepts one quotation (submits SQ).

3. ``schedule_ltl_pickup`` → POST /svc/quoteOrderFlow
   Reads the accepted SQ's ``quote_or_offer_transaction_id`` +
   ``quote_or_offer_id`` to call quoteOrderFlow.
   Sets ``awb_number`` (PRO / BOL#), attaches documents.

4. ``get_documents``  → POST /svc/documentDownloadFlow
5. ``track_shipment`` → POST /svc/searchShipmentsFlow
6. ``cancel_shipment``→ POST /svc/integratedCancelFlow

Authentication
--------------
OAuth 2.0 client-credentials. Primary: ``client_id`` and ``client_secret`` on
Freight Carrier Settings, POST to ``auth_url`` (e.g. staging
``https://auth.staging-wwex.com/oauth/token``), with ``audience`` on FCS when
required. Alternate: Frappe **Connected App** (token URI and ``query_parameters``
for audience). Token is cached in ``frappe.cache`` keyed by FCS record name.

All requests wrap in ``{"request": {...}, "correlationId": "<uuid>"}``.

**Base URL (SpeedShip)**
Use the REST host root only (e.g. ``https://www.speedship.com``). Paths such as ``/svc/shopFlow``
are appended by the client — do **not** set base URL to ``https://www.speedship.com/svc``.
See [SpeedShip](https://www.speedship.com/svc/) service root for orientation; API calls use the
assembled ``{base_url}/svc/…`` URLs.

**Pickup date/time**
``shopFlow`` expects ``ShipmentV3.shipmentDate`` exactly as ``yyyy-MM-dd HH:mm:ss`` (24-hour, no
fractional seconds), e.g. ``2023-03-15 18:49:00``.

**Weights**
WWEX validates ``ShippedItemV3.weight`` with units **LB** or **OZ** only — not KG/Gram. Packages
built in SI (e.g. Kg parcel weight) are converted here before POST.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

import frappe
import httpx
from frappe import _
from frappe.utils import flt, get_time, now, getdate
from frappe.utils.file_manager import save_file

from shipstation_integration.base_ltl import (
	BaseLTL,
	persist_shipment_ltl_fields,
	require_submitted_shipment_for_ltl,
)
from shipstation_integration.ltl import (
	LTL_SUPPORTED_DIMENSION_UOMS,
	ShipstationLTL,
	format_freight_class,
)
from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.utils import get_shipment_company_for_ltl

if TYPE_CHECKING:
	from erpnext.stock.doctype.shipment.shipment import Shipment

TOKEN_REFRESH_BUFFER = 60  # refresh token this many seconds before expiry

# ShipEngine-style ``pkg["weight"]["unit"]`` values from ``build_packages_from_sdn`` (plural keys)
SHIPENGINE_WEIGHT_UNIT_TO_LB = {
	"pounds": 1.0,
	"kilograms": 2.20462,
	"ounces": 1 / 16,
	"grams": 0.00220462,
}


WWEX_ACCESSORIAL_FLAGS: dict[str, str] = {
	"additional_insurance_or_excess_value": "insuranceRequestFlag",
	"appointment_required_at_delivery": "appointmentDeliveryFlag",
	"construction_site_delivery": "constructionSiteDeliveryFlag",
	"construction_site_pickup": "constructionSitePickupFlag",
	"direct_delivery_only": "directDeliveryOnlyFlag",
	"hold_at_terminal": "holdAtTerminalFlag",
	"inside_delivery": "insideDeliveryFlag",
	"inside_pickup": "insidePickupFlag",
	"carrier_terminal_pickup": "carrierTerminalPickupFlag",
	"lift_gate_required_at_delivery": "liftgateDeliveryFlag",
	"lift_gate_required_at_pickup": "liftgatePickupFlag",
	"notify_before_delivery": "notifyBeforeDeliveryFlag",
	"protect_from_cold": "protectionFromColdFlag",
	"residential_delivery": "residentialDeliveryFlag",
	"residential_pickup": "residentialPickupFlag",
	"sort_and_segregate": "sortAndSegregateFlag",
	"tradeshow_delivery": "tradeshowDeliveryFlag",
	"tradeshow_pickup": "tradeshowPickupFlag",
}

# Fields that need accompanying name strings (flag + name pair)
WWEX_TRADESHOW_FIELDS: dict[str, str] = {
	"tradeshowDeliveryFlag": "tradeshowDeliveryName",
	"tradeshowPickupFlag": "tradeshowPickupName",
}


class WwexLTL(BaseLTL):
	"""WWEX LTL provider — SpeedShip API v1."""

	def __init__(self):
		self.provider = "WWEX"

	@staticmethod
	def log_wwex_error(title: str, message: str, **kwargs) -> None:
		"""Persist diagnostics outside the request transaction (survives frappe.throw rollback)."""
		frappe.log_error(title=title, message=message, defer_insert=True, **kwargs)

	@staticmethod
	def truncate_for_ui(text: str, limit: int = 2000) -> str:
		text = (text or "").strip()
		if len(text) <= limit:
			return text
		return text[:limit] + "…"

	def get_fcs(self, doc: Shipment | None, settings_name: str | None):
		if settings_name and frappe.db.exists("Freight Carrier Settings", settings_name):
			return frappe.get_doc("Freight Carrier Settings", settings_name)
		co = get_shipment_company_for_ltl(doc)
		supplier = getattr(doc, "preferred_carrier", None)
		fc = get_freight_carrier_settings(co, supplier) if co and supplier else None
		if not fc:
			frappe.throw(
				_("No WWEX Freight Carrier Settings found for company {0} and supplier {1}.").format(
					co, supplier
				)
			)
		return fc

	def get_token(self, fc) -> str:
		"""Return a Bearer token via client_credentials grant.

		Priority:
		1. ``client_id`` / ``client_secret`` on FCS → POST to ``fc.auth_url``.
		2. Connected App → uses its client_id/secret and token_uri.

		The ``audience`` FCS field (e.g. "wwex-apig") is added to the token
		request when set. It is required by WWEX but optional for other providers.
		"""
		cache_key = f"wwex_token:{fc.name}"
		cached = frappe.cache.get_value(cache_key)
		if cached and cached.get("expires_at", 0) > time.time() + TOKEN_REFRESH_BUFFER:
			return cached["access_token"]

		client_id = fc.get_password("client_id", raise_exception=False) or ""
		client_secret = fc.get_password("client_secret", raise_exception=False) or ""

		if client_id and client_secret:
			token_url = fc.auth_url or ""
			if not token_url:
				frappe.throw(
					_(
						"Auth / Token URL is not set on Freight Carrier Settings <b>{0}</b>. "
						"For WWEX production set it to https://auth.wwex.com/oauth/token."
					).format(fc.name),
					title=_("Missing Token URL"),
				)
			data = {
				"grant_type": "client_credentials",
				"client_id": client_id,
				"client_secret": client_secret,
			}
			if fc.audience:
				data["audience"] = fc.audience
		elif getattr(fc, "connected_app", None):
			app = frappe.get_doc("Connected App", fc.connected_app)
			token_url = app.token_uri
			data = {
				"grant_type": "client_credentials",
				"client_id": app.client_id,
				"client_secret": app.get_password("client_secret"),
			}
			for row in app.get("query_parameters") or []:
				data[row.key] = row.value
		else:
			frappe.throw(
				_(
					"No credentials found on Freight Carrier Settings <b>{0}</b>. "
					"Set Client ID, Client Secret, and Auth / Token URL, or link a Connected App."
				).format(fc.name),
				title=_("Missing WWEX Credentials"),
			)

		with httpx.Client() as client:
			resp = client.post(token_url, data=data, timeout=30)
		self.raise_for_status(resp, "token")

		payload = self.decode_response_json(resp, _("OAuth token exchange"))
		access_token = payload["access_token"]
		expires_in = int(payload.get("expires_in", 3600))
		frappe.cache.set_value(
			cache_key,
			{"access_token": access_token, "expires_at": time.time() + expires_in},
			expires_in_sec=expires_in,
		)
		return access_token

	def raise_for_status(self, resp: httpx.Response, context: str = "") -> None:
		"""Raise with WWEX's error body surfaced in the UI and error log."""
		if resp.is_error:
			try:
				body = resp.json()
			except Exception:
				body = resp.text
			detail = body if isinstance(body, str) else frappe.as_json(body, indent=2)
			label = f" ({context})" if context else ""
			self.log_wwex_error(
				title=f"WWEX LTL {resp.status_code}{label}",
				message=detail,
			)
			frappe.throw(
				_("WWEX API error {0}{1}:\n{2}").format(resp.status_code, label, detail),
				title=_("WWEX LTL Error"),
			)

	def decode_response_json(self, resp: httpx.Response, context: str):
		"""Parse JSON body after HTTP success — WWEX occasionally returns blank/HTML via wrong base URL."""
		raw = resp.text if resp.content is not None else ""
		raw = raw or ""
		trimmed = raw.strip()
		req_url = str(getattr(resp.request, "url", "") or "")

		if not trimmed:
			detail = f"context={context}\nstatus_code={resp.status_code}\nurl={req_url}"
			self.log_wwex_error(title="WWEX LTL empty response", message=detail)
			frappe.throw(
				_(
					"The WWEX API returned an empty body for «{0}» (HTTP {1}). Usually this means "
					"<b>Domain / Base URL</b> on Freight Carrier Settings does not reach the WWEX REST host "
					"(wrong host, path, VPN, or a proxy/HTML page instead of JSON). Request URL was: {2}"
				).format(context, resp.status_code, req_url),
				title=_("WWEX LTL empty response"),
			)

		try:
			data = json.loads(trimmed)
		except json.JSONDecodeError:
			detail = f"context={context}\nstatus_code={resp.status_code}\nurl={req_url}\n\nbody:\n{raw}"
			self.log_wwex_error(title="WWEX LTL non-JSON response", message=detail)
			preview = raw[:600].strip()
			if len(raw) > 600:
				preview += "…"
			frappe.throw(
				_(
					"The WWEX API returned a non-JSON body for «{0}» (HTTP {1}). "
					"Check base URL / credentials environment. Preview:\n\n{2}"
				).format(context, resp.status_code, preview),
				title=_("WWEX LTL invalid response"),
			)

		if not isinstance(data, dict):
			frappe.throw(
				_("Unexpected WWEX response type for «{0}»: expected a JSON object.").format(context),
				title=_("WWEX LTL unexpected response"),
			)
		return data

	def headers(self, fc) -> dict:
		return {
			"Authorization": f"Bearer {self.get_token(fc)}",
			"Content-Type": "application/json",
			"Accept": "application/json",
		}

	def url(self, fc, path: str) -> str:
		return f"{(fc.base_url or '').rstrip('/')}/{path.lstrip('/')}"

	@staticmethod
	def wwex_shop_flow_shipment_date(doc: Shipment) -> str:
		"""Serialize pickup date/time for WWEX ``ShipmentV3.shipmentDate``.

		WWEX rejects values that do not parse as ``yyyy-MM-dd HH:mm:ss`` (ERPNext stores
		``pickup_from`` with fractional seconds like ``9:53:54.719844`` and uneven padding).
		"""
		raw_date = doc.get("pickup_date")
		if not raw_date:
			frappe.throw(
				_("Pickup Date is required for WWEX LTL quotes."),
				title=_("Missing pickup date"),
			)
		date_part = getdate(raw_date)
		pt = doc.get("pickup_from")
		if pt in (None, ""):
			from datetime import time as time_constructor

			t = time_constructor(12, 0, 0)
		else:
			try:
				t = get_time(pt)
			except Exception:
				frappe.throw(
					_("Pickup from time is invalid for WWEX: {0}. Use a recognizable time.").format(pt),
					title=_("Invalid pickup time"),
				)
			# Drop microseconds; strftime H:M:S ignores them but keeps types explicit
			if getattr(t, "microsecond", 0):
				t = t.replace(microsecond=0)
		return f"{date_part.strftime('%Y-%m-%d')} {t.strftime('%H:%M:%S')}"

	def post(self, fc, path: str, payload: dict) -> dict:
		"""Wrap payload in the standard WWEX envelope and POST."""
		body = {
			"request": payload,
			"correlationId": f"WWEX-ERP-{uuid.uuid4()}",
		}
		with httpx.Client() as client:
			resp = client.post(
				self.url(fc, path),
				json=body,
				headers=self.headers(fc),
				timeout=60,
			)
		self.raise_for_status(resp, path)
		return self.decode_response_json(resp, path)

	@staticmethod
	def wwex_address(info: dict) -> dict:
		"""Convert ShipstationLTL.get_address_and_contact_info output → WWEX address block."""
		addr = info["address"]
		contact = info["contact"]
		return {
			"address": {
				"addressLineList": [addr["address_line1"]]
				+ ([addr["address_line2"]] if addr.get("address_line2") else []),
				"locality": addr["city_locality"],
				"region": addr["state_province"],
				"postalCode": addr["postal_code"],
				"countryCode": addr["country_code"],
				"companyName": addr.get("company_name") or "",
				"phone": contact["phone_number"],
				"contactList": [
					{
						"firstName": (contact["name"] or "").split(" ")[0],
						"lastName": " ".join((contact["name"] or "").split(" ")[1:]) or "",
						"phone": contact["phone_number"],
						"contactType": "SENDER",  # overridden by caller for consignee
					}
				],
			}
		}

	DIM_UNIT = {"inches": "IN", "centimeters": "CM", "feet": "FT"}

	@staticmethod
	def shipengine_pkg_weight_value_to_pounds(pkg_weight: dict) -> float:
		"""Interpret ``ShipstationLTL`` package weight … value/unit as pounds."""
		raw = flt(pkg_weight.get("value"))
		ukey = ((pkg_weight.get("unit")) or "pounds").strip().lower()
		mult = SHIPENGINE_WEIGHT_UNIT_TO_LB.get(ukey, 1.0)
		return raw * mult

	@staticmethod
	def wwex_shipped_item_weight(pkg_weight: dict) -> dict[str, str]:
		"""WWEX ``ShippedItemV3.weight`` (and HU weight blocks) allows only LB or OZ—not KG or GM."""

		lb = WwexLTL.shipengine_pkg_weight_value_to_pounds(pkg_weight)
		if lb <= 0:
			lb = 0.01
		if lb < 1.0:
			return {"value": str(round(lb * 16, 3)), "unit": "OZ"}
		return {"value": str(round(lb, 3)), "unit": "LB"}

	def build_handling_units(
		self, doc: Shipment, packages: list[dict[str, Any]] | None = None
	) -> list[dict]:
		"""Build WWEX handlingUnitList from Shipment parcel groups."""
		if packages is None:
			packages = ShipstationLTL().build_packages_from_sdn(doc)

		units = []
		for pkg in packages:
			dims = pkg.get("dimensions", {})
			w_wwex = self.wwex_shipped_item_weight(pkg["weight"])
			dim_unit = self.DIM_UNIT.get(dims.get("unit", "inches"), "IN")
			items = []
			for _i in range(int(pkg.get("quantity", 1))):
				item: dict = {
					"commodityClass": format_freight_class(pkg.get("freight_class")),
					"commodityDescription": pkg.get("description") or "",
					"isHazMat": bool(doc.get("hazardous_material")),
					"weight": w_wwex,
					"quantity": 1,
				}
				if pkg.get("nmfc_code"):
					item["NMFCNbr"] = pkg["nmfc_code"]
				items.append(item)

			unit = {
				"packagingType": (pkg.get("code") or "PLT").upper(),
				"quantity": int(pkg.get("quantity", 1)),
				"weight": w_wwex,
				"billedDimension": {
					"length": {"value": str(dims.get("length", 0)), "unit": dim_unit},
					"width": {"value": str(dims.get("width", 0)), "unit": dim_unit},
					"height": {"value": str(dims.get("height", 0)), "unit": dim_unit},
				},
				"shippedItemList": items,
				"isMixedClass": False,
				"isStackable": False,
				"sortAndSegregateFlag": bool(doc.get("sort_and_segregate")),
			}
			units.append(unit)
		return units

	def build_accessorial_flags(self, doc: Shipment) -> dict:
		"""Return a dict of WWEX boolean accessorial flags from Shipment fields."""
		flags: dict = {}
		for field, wwex_key in WWEX_ACCESSORIAL_FLAGS.items():
			flags[wwex_key] = bool(doc.get(field))
		# Tradeshow names
		if flags.get("tradeshowDeliveryFlag"):
			flags["tradeshowDeliveryName"] = doc.get("tradeshow_delivery_name") or ""
		if flags.get("tradeshowPickupFlag"):
			flags["tradeshowPickupName"] = doc.get("tradeshow_pickup_name") or ""
		# Insurance declared value
		if flags.get("insuranceRequestFlag") and doc.get("shipment_amount"):
			flags["totalDeclaredValue"] = {
				"value": str(doc.get("shipment_amount")),
				"unit": "USD",
			}
		return flags

	def build_shop_payload(self, doc: Shipment) -> dict:
		"""Build a shopFlow LTL request payload from the Shipment doc."""
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)

		origin = self.wwex_address(origin_info)
		dest = self.wwex_address(dest_info)
		# Fix contactType for consignee
		for c in dest["address"]["contactList"]:
			c["contactType"] = "RECEIVER"

		packages = ltl.build_packages_from_sdn(doc)
		total_lb = sum(self.shipengine_pkg_weight_value_to_pounds(p["weight"]) for p in packages)
		if total_lb <= 0:
			total_lb = 0.01

		payload: dict = {
			"productType": "LTL",
			"shipment": {
				"shipmentDate": self.wwex_shop_flow_shipment_date(doc),
				"originAddress": origin,
				"destinationAddress": dest,
				"handlingUnitList": self.build_handling_units(doc, packages),
				"totalWeight": {
					"value": str(round(total_lb, 3)),
					"unit": "LB",
				},
				"totalHandlingUnitCount": len(packages),
				"pickupSpecialInstructions": doc.get("description_of_content") or "",
				"deliverySpecialInstructions": "",
			},
		}
		payload["shipment"].update(self.build_accessorial_flags(doc))
		return payload

	@staticmethod
	def wwex_offer_total_price_and_currency(offer: dict[str, Any]) -> tuple[float, str]:
		block = offer.get("totalOfferPrice") or offer.get("totalPrice") or offer.get("offerPrice") or {}
		if not isinstance(block, dict):
			return (0.0, "USD")
		raw_val = block.get("value")
		if raw_val is None and isinstance(block.get("amount"), dict):
			raw_val = block["amount"].get("value")
		try:
			price = float(raw_val or 0)
		except (TypeError, ValueError):
			price = 0.0
		ccy = block.get("unit") or ""
		if not ccy and isinstance(block.get("amount"), dict):
			ccy = block["amount"].get("currency") or ""
		return (price, ccy or "USD")

	def shop_flow_offer_list(self, payload: dict[str, Any]) -> list[Any]:
		"""Return offer rows from known shopFlow response envelope shapes."""

		def list_from(keys: tuple[str, ...], d: dict[str, Any] | None) -> list[Any] | None:
			if not isinstance(d, dict):
				return None
			for key in keys:
				v = d.get(key)
				if isinstance(v, list):
					return v
			return None

		if not isinstance(payload, dict):
			return []

		found = list_from(("offerList", "shipmentOffers", "offers"), payload)
		if found is not None:
			return found

		r = payload.get("response")
		found = list_from(("offerList", "shipmentOffers", "offers"), r if isinstance(r, dict) else None)
		if found is not None:
			return found

		if isinstance(r, dict):
			nested = r.get("response")
			found = list_from(
				("offerList", "shipmentOffers", "offers"), nested if isinstance(nested, dict) else None
			)
			if found is not None:
				return found

		return []

	def shop_flow_response_root(self, payload: dict[str, Any]) -> dict[str, Any]:
		"""Return the inner shopFlow ``response`` object from any known envelope shape."""
		if not isinstance(payload, dict):
			return {}
		inner = payload.get("response")
		if isinstance(inner, dict):
			nested = inner.get("response")
			if isinstance(nested, dict):
				return nested
			return inner
		return payload

	def shop_flow_diagnostic_messages(self, payload: dict[str, Any]) -> list[str]:
		"""Collect human-readable WWEX shopFlow failure reasons from a response envelope."""
		messages: list[str] = []

		correlation_id = payload.get("correlationId") if isinstance(payload, dict) else None
		if correlation_id:
			messages.append(_("Reference: {0}").format(correlation_id))

		response = self.shop_flow_response_root(payload)
		if response:
			shop_rs = response.get("shopRS")
			if isinstance(shop_rs, dict):
				ineligible = shop_rs.get("ineligibleReason")
				if ineligible:
					messages.append(str(ineligible))
			for key in ("message", "requestQuoteWarning"):
				val = response.get(key)
				if val:
					messages.append(str(val))

		def harvest_client_status(obj: Any, depth: int = 0) -> None:
			if depth > 24 or isinstance(obj, (str, int, float, bool)) or obj is None:
				return
			if isinstance(obj, dict):
				cs = obj.get("clientStatus")
				if isinstance(cs, dict):
					msg = cs.get("message")
					if msg:
						messages.append(str(msg))
					for k, errs in (cs.get("fieldMap") or {}).items():
						messages.append(f"{k}: {errs}")
				for v in obj.values():
					harvest_client_status(v, depth + 1)
			elif isinstance(obj, list):
				for item in obj:
					harvest_client_status(item, depth + 1)

		harvest_client_status(payload)
		return list(dict.fromkeys(m for m in messages if m))

	def shop_flow_raise_no_offers(self, payload: dict[str, Any]) -> None:
		"""Carrier returned HTTP 200 with no rate rows — expose WWEX diagnostics or fail loudly."""

		messages = self.shop_flow_diagnostic_messages(payload)
		try:
			dump = frappe.as_json(payload, indent=2)
		except Exception:
			dump = str(payload)
		self.log_wwex_error(
			title="WWEX shopFlow returned zero offers",
			message=(dump[:48000] if dump else "(empty)"),
		)
		body = "<br>".join(messages).strip()
		if body:
			frappe.throw(
				_("WWEX returned no freight offers.<br><br>{0}").format(body),
				title=_("No WWEX quotes"),
			)
		preview = frappe.utils.escape_html(self.truncate_for_ui(dump))
		frappe.throw(
			_("WWEX returned no freight offers. Response preview:<br><br><pre>{0}</pre>").format(preview),
			title=_("No WWEX quotes"),
		)

	def fetch_wwex_offers(self, doc: Shipment, fc) -> list[dict]:
		"""Call shopFlow and return raw offer rows (shape varies slightly by WWEX environment)."""

		payload = self.post(fc, "/svc/shopFlow", self.build_shop_payload(doc))
		offers = self.shop_flow_offer_list(payload)
		if not offers:
			self.shop_flow_raise_no_offers(payload)
		return offers

	def fetch_ltl_offers(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""Return WWEX shopFlow offers as normalized dicts without saving anything."""
		require_submitted_shipment_for_ltl(doc)
		fc = self.get_fcs(doc, settings_name)
		offers = self.fetch_wwex_offers(doc, fc)
		results: list[dict[str, Any]] = []
		for offer in offers:
			if not isinstance(offer, dict):
				continue
			vendor_raw = offer.get("primaryVendor") or offer.get("vendor") or offer.get("carrierDetail")
			vendor = vendor_raw if isinstance(vendor_raw, dict) else {}
			prods = offer.get("offeredProductList") or offer.get("offeredProducts") or []
			first_product = prods[0] if isinstance(prods, list) and prods else {}
			if not isinstance(first_product, dict):
				first_product = {}
			srs = first_product.get("shopRQShipment") or {}
			if not isinstance(srs, dict):
				srs = {}
			time_in_transit = srs.get("timeInTransit") or {}
			if not isinstance(time_in_transit, dict):
				time_in_transit = {}
			total_price, currency = self.wwex_offer_total_price_and_currency(offer)
			results.append(
				{
					"carrier_name": vendor.get("preferredName")
					or vendor.get("name")
					or vendor.get("carrierName")
					or "WWEX",
					"carrier_scac": vendor.get("scac") or vendor.get("carrierScac") or "",
					"offer_id": offer.get("offerId") or offer.get("shipmentOfferId") or "",
					"transaction_id": (
						offer.get("productTransactionId") or offer.get("shipmentProductTransactionId") or ""
					),
					"service_level": (
						time_in_transit.get("serviceDescription") or time_in_transit.get("serviceLevel") or ""
					),
					"total_price": total_price,
					"currency": currency,
					"transit_days": time_in_transit.get("transitDays"),
					"estimated_delivery_date": time_in_transit.get("estimatedDeliveryDate"),
					"expiration_date": offer.get("expirationDate"),
					"is_spot_quote": False,
					"charges": [],
				}
			)
		if not results and offers:
			try:
				dump = frappe.as_json(offers, indent=2)
			except Exception:
				dump = str(offers)
			self.log_wwex_error(
				title="WWEX shopFlow offers could not be parsed",
				message=dump[:48000] if dump else "(empty)",
			)
			preview = self.truncate_for_ui(dump)
			frappe.throw(
				_("WWEX returned offer rows but they could not be parsed. Response preview:\n\n{0}").format(
					preview
				),
				title=_("No WWEX quotes"),
			)
		return results

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Call shopFlow and create one Shipment Quotation per carrier offer returned."""
		require_submitted_shipment_for_ltl(doc)
		fc = self.get_fcs(doc, settings_name)
		offers = self.fetch_wwex_offers(doc, fc)

		saved = 0
		for offer in offers:
			vendor_raw = offer.get("primaryVendor") or offer.get("vendor") or offer.get("carrierDetail")
			vendor = vendor_raw if isinstance(vendor_raw, dict) else {}
			carrier_name = vendor.get("preferredName") or vendor.get("name") or "WWEX"
			scac = vendor.get("scac") or vendor.get("carrierScac") or ""
			offer_id = offer.get("offerId") or offer.get("shipmentOfferId") or ""
			txn_id = offer.get("productTransactionId") or offer.get("shipmentProductTransactionId") or ""

			# Dig into the first offered product for transit/service details
			prods = offer.get("offeredProductList") or offer.get("offeredProducts") or []
			first_product = prods[0] if isinstance(prods, list) and prods else {}
			if not isinstance(first_product, dict):
				first_product = {}
			srs = first_product.get("shopRQShipment") or {}
			if not isinstance(srs, dict):
				srs = {}
			time_in_transit = srs.get("timeInTransit") or {}
			if not isinstance(time_in_transit, dict):
				time_in_transit = {}
			transit_days = time_in_transit.get("transitDays")
			service_level = (
				time_in_transit.get("serviceDescription") or time_in_transit.get("serviceLevel") or ""
			)

			supplier_name = (
				frappe.db.get_value("Supplier", {"ltl_carrier_scac": scac, "is_transporter": 1}, "name")
				if scac
				else None
			)

			total_price, _currency_ignore = self.wwex_offer_total_price_and_currency(offer)

			sq = frappe.new_doc("Shipment Quotation")
			sq.shipment = doc.name
			sq.carrier = supplier_name or carrier_name
			sq.carrier_scac = scac
			sq.quote_or_offer_id = offer_id
			sq.quote_or_offer_transaction_id = txn_id
			sq.service_level = service_level
			sq.grand_total = total_price
			sq.pickup_date = doc.get("pickup_date")
			if transit_days is not None:
				sq.estimated_delivery_days = float(transit_days)
			sq.insert(ignore_permissions=True)
			saved += 1

		return _("{0} WWEX carrier offer(s) saved as Shipment Quotation(s).").format(saved)

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Book the accepted offer via quoteOrderFlow and attach the BOL."""
		require_submitted_shipment_for_ltl(doc)
		fc = self.get_fcs(doc, settings_name)

		accepted_sq_name = doc.accepted_quotation or frappe.db.get_value(
			"Shipment Quotation", {"shipment": doc.name, "docstatus": 1}, "name"
		)
		if not accepted_sq_name:
			frappe.throw(_("No accepted Shipment Quotation found. Please accept a quote first."))

		sq = frappe.get_doc("Shipment Quotation", accepted_sq_name)
		txn_id = sq.quote_or_offer_transaction_id
		offer_id = sq.quote_or_offer_id

		if not txn_id or not offer_id:
			frappe.throw(_("The accepted Shipment Quotation is missing WWEX transaction/offer IDs."))

		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)

		origin = self.wwex_address(origin_info)
		dest = self.wwex_address(dest_info)
		for c in dest["address"]["contactList"]:
			c["contactType"] = "RECEIVER"

		payload = {
			"shipmentProductTransactionId": txn_id,
			"shipmentOfferId": offer_id,
			"shipment": {
				"originAddress": origin,
				"destinationAddress": dest,
				"shipmentReferenceList": [
					{"type": "Shipment Reference 1", "value": doc.name, "isPrintAsBarCode": True}
				],
			},
			"isSelfScheduled": False,
			"pickupDate": str(doc.get("pickup_date") or ""),
			"readyTime": "08:00:00",
			"closeTime": "17:00:00",
			"pickupSpecialInstructions": doc.get("description_of_content") or "",
			"deliverySpecialInstructions": "",
		}

		response = self.post(fc, "/svc/quoteOrderFlow", payload)
		result = response.get("response") or response

		bol_number = result.get("bolNumber") or result.get("proNumber") or ""
		pro_number = result.get("proNumber") or bol_number
		pickup_txn_id = result.get("pickupTxnId") or result.get("pickupTransactionId") or ""

		dt, dn = doc.doctype, doc.name
		updates = {
			"carrier": doc.preferred_carrier,
			"carrier_service": sq.service_level,
			"awb_number": pro_number or bol_number,
			"shipment_id": txn_id,
		}
		if pickup_txn_id:
			updates["pickup_id"] = pickup_txn_id
		persist_shipment_ltl_fields(dt, dn, updates)

		# Attempt to download and attach the BOL immediately
		docs_saved = False
		try:
			docs = self.get_documents(doc, settings_name=fc.name)
			now_dt = now().split(".")[0]
			for d in docs:
				content = d.get("content") or ""
				if content:
					raw = base64.b64decode(content)
					save_file(
						f"{doc.name}-{d.get('document_type','BOL')}-{now_dt}.pdf",
						raw,
						"Shipment",
						doc.name,
					)
			docs_saved = bool(docs)
		except Exception:
			self.log_wwex_error(
				title="WWEX: Error attaching documents",
				message=frappe.get_traceback(),
				reference_doctype="Shipment",
				reference_name=doc.name,
			)

		msg = _("WWEX pickup confirmed. BOL/PRO: {0}").format(pro_number or bol_number)
		if docs_saved:
			msg += " " + _("Documents have been attached.")
		return msg

	def cancel_shipment(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Cancel via integratedCancelFlow using the stored transaction IDs."""
		fc = self.get_fcs(doc, settings_name)

		shipment_txn_id = doc.get("shipment_id") or ""
		pickup_txn_id = doc.get("pickup_id") or ""

		cancel_list = []
		if shipment_txn_id:
			cancel_list.append({"productTransactionId": shipment_txn_id})
		if pickup_txn_id and pickup_txn_id != shipment_txn_id:
			cancel_list.append({"productTransactionId": pickup_txn_id})

		if not cancel_list:
			frappe.throw(_("No WWEX transaction IDs found on this Shipment to cancel."))

		response = self.post(fc, "/svc/integratedCancelFlow", {"cancelRQList": cancel_list})
		result = response.get("response") or response
		return result.get("confirmationNumber") or result.get("cancellationId") or "cancelled"

	def track_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""Track via searchShipmentsFlow using the BOL/PRO number."""
		fc = self.get_fcs(doc, settings_name)
		bol = doc.get("awb_number") or doc.get("shipment_id") or doc.name

		# Try to find the SCAC from the accepted quotation
		scac = ""
		accepted_sq_name = doc.get("accepted_quotation") or frappe.db.get_value(
			"Shipment Quotation", {"shipment": doc.name, "docstatus": 1}, "name"
		)
		if accepted_sq_name:
			scac = frappe.db.get_value("Shipment Quotation", accepted_sq_name, "carrier_scac") or ""

		payload = {
			"trackingInfoList": [bol],
			"type": "BOL",
		}
		if scac:
			payload["scac"] = scac

		response = self.post(fc, "/svc/searchShipmentsFlow", payload)
		return response.get("response") or response

	def get_documents(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""Download BOL and related documents via documentDownloadFlow."""
		fc = self.get_fcs(doc, settings_name)
		txn_id = doc.get("shipment_id") or ""
		if not txn_id:
			return []

		payload = {
			"downloadMode": "MULTIPLE",
			"docTypes": ["BILL_OF_LADING"],
			"transactionType": "LTL",
			"referenceMap": {
				"PRODUCT_TRANSACTION_ID": txn_id,
			},
		}

		response = self.post(fc, "/svc/documentDownloadFlow", payload)
		result = response.get("response") or response
		raw_docs = result.get("documents") or result.get("documentList") or []

		documents = []
		for d in raw_docs:
			documents.append(
				{
					"document_type": d.get("documentType") or d.get("docType") or "BOL",
					"format": "PDF",
					"content": d.get("content") or d.get("base64Content") or "",
					"file_name": d.get("fileName") or f"{d.get('documentType','BOL')}.pdf",
				}
			)
		return documents

	def book_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		# WWEX booking goes through schedule_ltl_pickup (quoteOrderFlow)
		raise NotImplementedError("Use schedule_ltl_pickup for WWEX booking.")

	def list_ltl_carriers(
		self,
		settings_name: str | None = None,
		create_transporters: bool = False,
		company: str | None = None,
		supplier: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		# WWEX dynamically returns carriers per-shipment via shopFlow; no static carrier list
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
		return [
			{"value": "PLT", "label": "Pallet"},
			{"value": "SKD", "label": "Skid"},
			{"value": "CTN", "label": "Carton"},
			{"value": "CRT", "label": "Crate"},
			{"value": "BAG", "label": "Bag"},
			{"value": "DRM", "label": "Drum"},
			{"value": "RLL", "label": "Roll"},
			{"value": "PCK", "label": "Package"},
		]

	def get_carrier_service_levels(
		self, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		return []

	def get_accessorial_service_fields(self, doc: Shipment, settings_name: str | None = None) -> dict:
		supported = list(WWEX_ACCESSORIAL_FLAGS.keys())
		return {"supported": supported, "unsupported": []}

	def get_shipment_dimension_uoms(self) -> dict:
		return {
			"length_uom": list(LTL_SUPPORTED_DIMENSION_UOMS),
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
			missing.append("Shipment Delivery Note items (for package details)")
		if missing:
			return _("The following fields are required for WWEX quoting: {0}").format(", ".join(missing))
		return None

	def supports_quote_or_spot_quote(self, doc: Shipment, settings_name: str | None = None) -> dict:
		return {"supports_quote": True, "supports_spot_quote": False}
