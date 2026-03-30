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
OAuth 2.0 client-credentials flow via Frappe Connected App.
Token endpoint: ``https://auth.staging-wwex.com/oauth/token`` (staging).
The Connected App ``query_parameters`` child table should contain
``audience = staging-wwex-apig`` (staging) or the production equivalent.
Token is cached in ``frappe.cache`` keyed by FCS record name.

All requests wrap in ``{"request": {...}, "correlationId": "<uuid>"}``.
"""
from __future__ import annotations

import base64
import time
import uuid
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

_TOKEN_REFRESH_BUFFER = 60  # refresh token this many seconds before expiry


# ---------------------------------------------------------------------------
# Accessorial flag map: Shipment field name → WWEX shopFlow boolean key
# ---------------------------------------------------------------------------
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
				_("No WWEX Freight Carrier Settings found for company {0} and supplier {1}.").format(
					co, supplier
				)
			)
		return fc

	def _get_token(self, fc) -> str:
		cache_key = f"wwex_token:{fc.name}"
		cached = frappe.cache.get_value(cache_key)
		if cached and cached.get("expires_at", 0) > time.time() + _TOKEN_REFRESH_BUFFER:
			return cached["access_token"]

		app = frappe.get_doc("Connected App", fc.connected_app)
		data = {
			"grant_type": "client_credentials",
			"client_id": app.client_id,
			"client_secret": app.get_password("client_secret"),
		}
		# Extra params (e.g. audience=staging-wwex-apig) stored as query_parameters rows
		for row in app.get("query_parameters") or []:
			data[row.key] = row.value

		with httpx.Client() as client:
			resp = client.post(app.token_uri, data=data, timeout=30)
			resp.raise_for_status()
			payload = resp.json()

		access_token = payload["access_token"]
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
		return f"{(fc.base_url or '').rstrip('/')}/{path.lstrip('/')}"

	def _post(self, fc, path: str, payload: dict) -> dict:
		"""Wrap payload in the standard WWEX envelope and POST."""
		body = {
			"request": payload,
			"correlationId": f"WWEX-ERP-{uuid.uuid4()}",
		}
		with httpx.Client() as client:
			resp = client.post(
				self._url(fc, path),
				json=body,
				headers=self._headers(fc),
				timeout=60,
			)
		resp.raise_for_status()
		return resp.json()

	# ------------------------------------------------------------------
	# Address helpers
	# ------------------------------------------------------------------

	@staticmethod
	def _wwex_address(info: dict) -> dict:
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

	# ------------------------------------------------------------------
	# Payload builders
	# ------------------------------------------------------------------

	def _build_handling_units(self, doc: Shipment) -> list[dict]:
		"""Build WWEX handlingUnitList from Shipment parcel groups."""
		# Reuse ShipstationLTL's parcel grouping logic
		ltl = ShipstationLTL()
		packages = ltl.build_packages_from_sdn(doc)

		units = []
		for pkg in packages:
			dims = pkg.get("dimensions", {})
			items = []
			for i in range(int(pkg.get("quantity", 1))):
				item: dict = {
					"commodityClass": str(pkg.get("freight_class", "50")),
					"commodityDescription": pkg.get("description") or "",
					"isHazMat": bool(doc.get("hazardous_material")),
					"weight": {
						"value": str(pkg["weight"]["value"]),
						"unit": pkg["weight"]["unit"].upper()[:2],  # LB / KG
					},
					"quantity": 1,
				}
				if pkg.get("nmfc_code"):
					item["NMFCNbr"] = pkg["nmfc_code"]
				items.append(item)

			unit = {
				"packagingType": (pkg.get("code") or "PLT").upper(),
				"quantity": int(pkg.get("quantity", 1)),
				"weight": {
					"value": str(pkg["weight"]["value"]),
					"unit": pkg["weight"]["unit"].upper()[:2],
				},
				"billedDimension": {
					"length": {"value": str(dims.get("length", 0)), "unit": dims.get("unit", "IN").upper()[:2]},
					"width": {"value": str(dims.get("width", 0)), "unit": dims.get("unit", "IN").upper()[:2]},
					"height": {"value": str(dims.get("height", 0)), "unit": dims.get("unit", "IN").upper()[:2]},
				},
				"shippedItemList": items,
				"isMixedClass": False,
				"isStackable": False,
				"sortAndSegregateFlag": bool(doc.get("sort_and_segregate")),
			}
			units.append(unit)
		return units

	def _build_accessorial_flags(self, doc: Shipment) -> dict:
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

	def _build_shop_payload(self, doc: Shipment) -> dict:
		"""Build a shopFlow LTL request payload from the Shipment doc."""
		ltl = ShipstationLTL()
		origin_info = ltl.get_address_and_contact_info(doc, ship_from=True)
		dest_info = ltl.get_address_and_contact_info(doc, ship_from=False)

		origin = self._wwex_address(origin_info)
		dest = self._wwex_address(dest_info)
		# Fix contactType for consignee
		for c in dest["address"]["contactList"]:
			c["contactType"] = "RECEIVER"

		payload: dict = {
			"productType": "LTL",
			"shipment": {
				"shipmentDate": str(doc.get("pickup_date") or ""),
				"originAddress": origin,
				"destinationAddress": dest,
				"handlingUnitList": self._build_handling_units(doc),
				"totalWeight": {
					"value": sum(p["weight"]["value"] for p in ShipstationLTL().build_packages_from_sdn(doc)),
					"unit": "LB",
				},
				"totalHandlingUnitCount": len(self._build_handling_units(doc)),
				"pickupSpecialInstructions": doc.get("description_of_content") or "",
				"deliverySpecialInstructions": "",
			},
		}
		payload["shipment"].update(self._build_accessorial_flags(doc))
		return payload

	# ------------------------------------------------------------------
	# BaseLTL interface
	# ------------------------------------------------------------------

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Call shopFlow and create one Shipment Quotation per carrier offer returned."""
		fc = self._get_fcs(doc, settings_name)
		response = self._post(fc, "/svc/shopFlow", self._build_shop_payload(doc))

		offers = response.get("response", {}).get("shipmentOfferList", [])
		if not offers:
			frappe.msgprint(_("WWEX returned no carrier offers for this shipment."))
			return None

		saved = 0
		for offer in offers:
			carrier_name = offer.get("carrierName") or "WWEX"
			scac = offer.get("scac") or ""
			offer_id = offer.get("shipmentOfferId") or ""
			txn_id = response.get("response", {}).get("shipmentProductTransactionId") or ""

			# Find or create a Supplier for this carrier
			supplier_name = (
				frappe.db.get_value("Supplier", {"ltl_carrier_scac": scac, "is_transporter": 1}, "name")
				if scac
				else None
			)

			price = offer.get("price") or {}
			net = float(price.get("netPrice") or price.get("totalPrice") or 0)
			transit_days = offer.get("transitDays")

			sq = frappe.new_doc("Shipment Quotation")
			sq.shipment = doc.name
			sq.carrier = supplier_name or carrier_name
			sq.carrier_scac = scac
			sq.quote_or_offer_id = offer_id
			sq.quote_or_offer_transaction_id = txn_id
			sq.service_level = offer.get("serviceDescription") or offer.get("serviceType") or ""
			sq.grand_total = net
			sq.pickup_date = doc.get("pickup_date")
			if transit_days is not None:
				sq.estimated_delivery_days = float(transit_days)
			sq.insert(ignore_permissions=True)
			saved += 1

		frappe.db.commit()
		return _("{0} WWEX carrier offer(s) saved as Shipment Quotation(s).").format(saved)

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""Book the accepted offer via quoteOrderFlow and attach the BOL."""
		fc = self._get_fcs(doc, settings_name)

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

		origin = self._wwex_address(origin_info)
		dest = self._wwex_address(dest_info)
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

		response = self._post(fc, "/svc/quoteOrderFlow", payload)
		result = response.get("response") or response

		bol_number = result.get("bolNumber") or result.get("proNumber") or ""
		pro_number = result.get("proNumber") or bol_number
		pickup_txn_id = result.get("pickupTxnId") or result.get("pickupTransactionId") or ""

		dt, dn = doc.doctype, doc.name
		frappe.set_value(dt, dn, "carrier", doc.preferred_carrier)
		frappe.set_value(dt, dn, "carrier_service", sq.service_level)
		frappe.set_value(dt, dn, "awb_number", pro_number or bol_number)
		if pickup_txn_id:
			frappe.set_value(dt, dn, "pickup_id", pickup_txn_id)
		frappe.set_value(dt, dn, "shipment_id", txn_id)

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
			frappe.log_error(
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
		fc = self._get_fcs(doc, settings_name)

		shipment_txn_id = doc.get("shipment_id") or ""
		pickup_txn_id = doc.get("pickup_id") or ""

		cancel_list = []
		if shipment_txn_id:
			cancel_list.append({"productTransactionId": shipment_txn_id})
		if pickup_txn_id and pickup_txn_id != shipment_txn_id:
			cancel_list.append({"productTransactionId": pickup_txn_id})

		if not cancel_list:
			frappe.throw(_("No WWEX transaction IDs found on this Shipment to cancel."))

		response = self._post(fc, "/svc/integratedCancelFlow", {"cancelRQList": cancel_list})
		result = response.get("response") or response
		return result.get("confirmationNumber") or result.get("cancellationId") or "cancelled"

	def track_shipment(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""Track via searchShipmentsFlow using the BOL/PRO number."""
		fc = self._get_fcs(doc, settings_name)
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

		response = self._post(fc, "/svc/searchShipmentsFlow", payload)
		return response.get("response") or response

	def get_documents(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""Download BOL and related documents via documentDownloadFlow."""
		fc = self._get_fcs(doc, settings_name)
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

		response = self._post(fc, "/svc/documentDownloadFlow", payload)
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

	# ------------------------------------------------------------------
	# Remaining BaseLTL stubs (WWEX is a broker — no carrier management)
	# ------------------------------------------------------------------

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
			"length_uom": ["Inch"],
			"weight_uom": ["Pound"],
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
