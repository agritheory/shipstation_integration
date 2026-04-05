# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
LTL management for ShipStation API.

This module provides functionality for managing LTL carriers, their services and package (aka
container) types, requesting Quotes or Spot Quotes, scheduling pickups, and generating a BOL using
the ShipStation API v-beta.
"""

import base64
import json
import re

import frappe
import httpx
from erpnext.stock.doctype.shipment.shipment import Shipment
from erpnext.stock.doctype.shipment_parcel.shipment_parcel import ShipmentParcel
from frappe import _
from frappe.utils import add_days, comma_or, flt, get_link_to_form, now
from frappe.utils.file_manager import save_file

from shipstation_integration.base_ltl import BaseLTL
from shipstation_integration.carriers import get_or_create_transporter
from shipstation_integration.rates import DIMENSION_UOM_MAP, WEIGHT_UOM_MAP
from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.utils import (
	get_error_message,
	get_shipment_company_for_ltl,
	get_shipstation_settings_optional,
)


# Module-level conversion tables used by ShipstationLTL class-body comprehensions.
# They must live here because Python dict comprehensions in a class body have
# their own scope and cannot reference sibling class variables.
CANONICAL_TO_LB: dict[str, float] = {
	"pound": 1.0,
	"ounce": 0.0625,
	"kilogram": 2.20462,
	"gram": 0.00220462,
}
CANONICAL_TO_CUFT: dict[str, float] = {"inch": 1.0 / 1728.0, "centimeter": 1.0 / 28316.85}


class ShipstationLTL(BaseLTL):
	def __init__(self):
		"""
		The self.provider value should be the name of the API service provider
		"""
		self.provider = "Shipstation"

		# Maps ERPNext UOMs (and all aliases from rates.WEIGHT/DIMENSION_UOM_MAP) to the
		# plural forms the ShipEngine LTL API expects.
		singular_to_plural = {
			"inch": "inches",
			"centimeter": "centimeters",
			"pound": "pounds",
			"kilogram": "kilograms",
			"gram": "grams",
			"ounce": "ounces",
		}
		self.uom_map = {
			"length": {k: singular_to_plural.get(v, v) for k, v in DIMENSION_UOM_MAP.items()},
			"weight": {k: singular_to_plural.get(v, v) for k, v in WEIGHT_UOM_MAP.items()},
			"density": {"Pound/Cubic Foot": "lb/ft3"},
		}

		# Maps UI field name in Shipment to accepted payload values (excludes response-only ones)
		# List found here: https://www.shipengine.com/docs/ltl/list-accessorial-services/
		self.accessorial_services_map = {
			"additional_insurance_or_excess_value": "INS",
			"appointment_required_at_delivery": "APTD",
			"appointment_required_at_pickup": "APTP",
			"carrier_terminal_pickup": "NA1",  # Not supported
			"collect_on_delivery": "COD",
			"construction_site_delivery": "CNSTD",
			"construction_site_pickup": "CNSTP",
			"direct_delivery_only": "NA2",  # Not supported
			"hazardous_material": "HAZ",
			"hold_at_terminal": "NA3",  # Not supported
			"in_bond_shipment": "INBD",
			"inside_delivery": "IDL",
			"inside_pickup": "IPU",
			"lift_gate_required_at_delivery": "LFTD",
			"lift_gate_required_at_pickup": "LFTP",
			"limited_access_delivery": "LTDAD",
			"limited_access_pickup": "LTDAP",
			"marked_or_tagged": "MARK",
			"notify_before_delivery": "MNC",  # must notify consignee
			"over_dimension_excessive_weight": "OVR",
			"perishable": "PPD",
			"poisonous_material": "PSN",
			"protect_from_cold": "PSC",
			"protect_from_heat": "PSH",
			"residential_delivery": "RES",
			"residential_pickup": "REP",
			"secured_limited_access_delivery": "SLTDAD",
			"secured_limited_access_pickup": "SLTDAP",
			"single_shipment": "SS",
			"sort_and_segregate": "SRT",
			"tradeshow_delivery": "EBD",
			"tradeshow_pickup": "EBD",
		}

		self.accessorial_services_code_to_field = {
			v: k for k, v in self.accessorial_services_map.items()
		}

	@frappe.whitelist()
	def list_ltl_carriers(
		self,
		settings_name: str | None = None,
		create_transporters: bool = False,
		company: str | None = None,
		supplier: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		"""
		List all LTL carriers connected to the ShipStation account.

		Args:
		settings_name: Deprecated, unused (LTL auth uses Freight Carrier Settings only).
		create_transporters: If True, create Supplier records with is_transporter=1
		for each carrier that doesn't already exist
		company: Company for Freight Carrier Settings (with supplier) when doc is not passed
		supplier: Supplier (transporter) name for Freight Carrier Settings when doc is not passed
		doc: Optional Shipment; uses Preferred Carrier and company from addressing fields

		Returns:
		List of carrier dicts with carrier_id, carrier_code, name, supplier, etc.
		"""
		del settings_name  # unused; FCS resolved via company+supplier
		auth_doc = self.ltl_auth_doc(doc=doc, company=company, supplier=supplier)
		base_url, headers = self.get_base_url_and_headers(auth_doc)
		data: dict = {}
		try:
			with httpx.Client() as client:
				response = client.get(
					f"{base_url}/v-beta/ltl/carriers",
					headers=headers,
				)
				response.raise_for_status()
				data = response.json()
				carriers = data.get("carriers", [])
				result = []
				for c in carriers:
					formatted = self.format_ltl_carrier(c)

					# Optionally create transporter Supplier
					if create_transporters and formatted.get("name"):
						supplier_name = get_or_create_transporter(
							formatted["name"], formatted.get("carrier_id"), formatted.get("scac")
						)
						formatted["supplier"] = supplier_name

					result.append(formatted)

				return result

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error getting LTL carriers",
				message=f"Error Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to get LTL carriers - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return []

		except Exception as e:
			frappe.log_error(title="Error getting LTL carriers", message=str(e))
			frappe.throw(_("Failed to get LTL carriers - {0}").format(str(e)))
			return []

	def get_carrier_id_for_supplier(
		self,
		supplier_name: str,
		settings_name: str | None = None,
		company: str | None = None,
	) -> str | None:
		"""
		Look up the ShipEngine carrier_id for a given Supplier (transporter) name.

		Uses Supplier.ltl_carrier_id when set, otherwise synced LTL carrier data on Shipstation
		Settings when available. Does not require ShipStation to be enabled if the supplier ID
		is stored on the Supplier record.

		Args:
		supplier_name: The Supplier document name (e.g., "UPS", "USPS")
		settings_name: Optional Shipstation Settings document name
		company: Optional company (reserved for future use)

		Returns:
		ShipEngine carrier_id (e.g., "100abcde-...") or None if not found
		"""
		if not supplier_name:
			return None

		settings = get_shipstation_settings_optional(settings_name)
		id_pattern = re.compile(
			"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
		)

		# Check Supplier record's ltl_carrier_id field
		carrier_id = frappe.get_value("Supplier", supplier_name, "ltl_carrier_id")
		# Validate carrier_id format (should be like "100abcde-...")
		if carrier_id and re.match(id_pattern, carrier_id):
			return carrier_id
		elif carrier_id:
			frappe.log_error(
				title="Invalid LTL carrier_id format in Supplier",
				message=f"Supplier '{supplier_name}' has invalid LTL carrier_id: {carrier_id}",
			)

		if not settings or not settings.shipstation_api_ltl_carrier_data:
			return None

		carrier_data = json.loads(settings.shipstation_api_ltl_carrier_data)

		# Look up by name (case-insensitive)
		supplier_name_lower = supplier_name.lower()
		for carrier in carrier_data:
			carrier_name = carrier.get("name", "")
			if carrier_name and carrier_name.lower() == supplier_name_lower:
				carrier_id = carrier.get("carrier_id")
				# Validate carrier_id format (should be like "100abcde-...")
				if carrier_id and re.match(id_pattern, carrier_id):
					return carrier_id
				frappe.log_error(
					title="Invalid LTL carrier_id format",
					message=f"Carrier '{carrier_name}' has invalid LTL carrier_id: {carrier_id}",
				)
				return None

			# Also check supplier field if it was stored
			carrier_supplier = carrier.get("supplier", "")
			if carrier_supplier and carrier_supplier.lower() == supplier_name_lower:
				carrier_id = carrier.get("carrier_id")
				if carrier_id and re.match(id_pattern, carrier_id):
					return carrier_id
				frappe.log_error(
					title="Invalid LTL carrier_id format",
					message=f"Carrier with supplier '{carrier_supplier}' has invalid LTL carrier_id: {carrier_id}",
				)
				return None

		# Not found - log for debugging
		available_carriers = [f"{c.get('name')} (supplier: {c.get('supplier')})" for c in carrier_data]
		frappe.log_error(
			title="Carrier not found for supplier",
			message=f"Looking for supplier: {supplier_name}\nAvailable carriers: {', '.join(available_carriers)}",
		)

		return None

	def get_package_type_options(
		self,
		carrier_id: str | None = None,
		settings_name: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		"""
		Returns a UI-friendly dict with label and value keys to populate dropdown options in the
		Shipment document's Shipment Parcel child table package_type field.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of dicts with "value" and "label" keys for use in select field
		"""
		if not carrier_id:
			return []
		packages = self.list_ltl_carrier_package_types(carrier_id, settings_name, doc)
		options = []
		for pkg in packages:
			label = pkg.get("name", pkg.get("code", ""))
			if not label:
				continue
			options.append(
				{
					"value": pkg.get("code"),
					"label": _(label),
				}
			)
		return options

	def get_carrier_service_levels(
		self, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		"""
		Returns a UI-friendly dict with label and value keys to populate dropdown options in the
		Shipment document's carrier_service_level field.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of dicts with "value" and "label" keys for use in select field
		"""
		options = []  # type: list[dict]
		if not doc.preferred_carrier:
			return options
		carrier_id = doc.carrier_id or self.get_carrier_id_for_supplier(
			doc.preferred_carrier, settings_name, get_shipment_company_for_ltl(doc)
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting LTL carrier service levels",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to collect supported carrier service levels.",
			)
			return options

		svc_levels = self.list_ltl_carrier_services(carrier_id, settings_name, doc)
		for svc in svc_levels:
			label = svc.get("name", svc.get("code", ""))
			if not label:
				continue
			options.append(
				{
					"value": svc.get("code"),
					"label": _(label),
				}
			)
		return options

	def get_accessorial_service_fields(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""
		Returns a dict of field names for supported and unsupported accessorial services - may be
		carrier-dependent or in general.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Dict with keys for "supported" and "unsupported" lists, that contain the Shipment document
		field names for supported and unsupported accessorial services.
		"""
		default = {"unsupported": list(self.accessorial_services_map.keys()), "supported": []}
		if not doc.preferred_carrier:
			return default
		carrier_id = doc.carrier_id or self.get_carrier_id_for_supplier(
			doc.preferred_carrier, settings_name, get_shipment_company_for_ltl(doc)
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting LTL carrier accessorial services",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to collect supported accessorial services.",
			)
			return default

		accessorial_svcs = self.list_ltl_carrier_accessorial_services(carrier_id, settings_name, doc)
		supported = []
		for acc_svc in accessorial_svcs:
			code = acc_svc.get("code", "").upper()
			field = self.accessorial_services_code_to_field.get(code)
			if field:
				supported.append(field)
		unsupported = [f for f in self.accessorial_services_map.keys() if f not in supported]
		return {"supported": supported, "unsupported": unsupported}

	def get_shipment_dimension_uoms(self) -> dict:
		"""
		UOM options for package length, weight, and density.

		Returns:
		Dict with "length_uom", "weight_uom", and "density_uom" keys. Each value is a list of
		ERPNext UOMs the API payloads can accept
		"""
		return {f"{k}_uom": list(v.keys()) for k, v in self.uom_map.items()}

	def validate_required_shipment_form_fields(
		self, doc: Shipment, settings_name: str | None = None
	) -> str:
		"""
		Returns a message to display in the UI noting any fields that are required to make a
		'get_ltl_quotes' API call that are missing data. It can skip fields that are already
		marked required.

		Args:
		doc: a Shipment document in ERPNext from which to retrieve the shipment info
		settings_name: Optional Shipstation Settings document name

		Returns:
		Message string to display in UI
		"""
		# TODO
		return ""

	def supports_quote_or_spot_quote(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""
		Convenience function that returns True/False whether a carrier in a Shipment doc supports
		requesting quotes and spot quotes.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Dict with keys for "supports_quote" and "supports_spot_quote" with boolean values
		"""
		default = {"supports_quote": False, "supports_spot_quote": False}
		if not doc.preferred_carrier:
			return default
		carrier_id = doc.carrier_id or self.get_carrier_id_for_supplier(
			doc.preferred_carrier, settings_name, get_shipment_company_for_ltl(doc)
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting whether LTL carrier supports quote or spot quotes",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to check if supports quotes or spot quotes.",
			)
			return default

		feats = self.list_ltl_carrier_features(carrier_id, settings_name, doc)
		return {"supports_quote": "quote" in feats, "supports_spot_quote": "spot_quote" in feats}

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""
		Gets LTL quote(s) in general or for a specific LTL carrier given Shipment data. If found,
		saves into Shipment Quotation docs and returns a summary message. Otherwise, displays
		message explaining no quotes or throws an error in the process.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Message string to display in UI or throws an error if encountered
		"""
		self.validate_carrier_and_id(doc, settings_name)
		carrier_id = doc.carrier_id

		quote_support = self.supports_quote_or_spot_quote(doc, settings_name)
		supports_spot_quote = quote_support.get("supports_spot_quote")
		is_spot_quote = bool(supports_spot_quote and doc.request_spot_quote)
		intro_text = ""

		# Handle case where regular quotes aren't supported
		if not quote_support.get("supports_quote"):
			msg = "This carrier doesn't support requesting quotes or spot quotes through the API."
			if not supports_spot_quote:
				msg += " Either call to get quotes and schedule a pickup or try a different carrier."
				return msg

			# Carrier does support spot quotes, but that wasn't requested in the UI
			elif supports_spot_quote and not doc.request_spot_quote:
				is_spot_quote = True
				intro_text = "This carrier only supports spot quotes through the API - requested spot quote instead.<br><br>"

		# Handle case where requested spot quote but they aren't supported
		elif not supports_spot_quote and doc.request_spot_quote:
			intro_text = "Spot Quote requested, but is not supported for this carrier - requested regular quotes instead.<br><br>"

		if is_spot_quote:
			quote_response = self.request_ltl_spot_quote(carrier_id, doc, settings_name)
		else:
			quote_response = self.request_ltl_quote(carrier_id, doc, settings_name)

		msg = self.save_ltl_quotes_as_shipment_quotations_and_display(
			doc, quote_response, is_spot_quote, intro_text
		)
		return msg

	def save_ltl_quotes_as_shipment_quotations_and_display(
		self,
		doc: Shipment,
		quote_response: list[dict],
		is_spot_quote: bool = False,
		intro_text: str = "",
	) -> str:
		"""
		Processes the returned quotes from get_ltl_quotes and saves into Shipment Quotations

		Args:
		doc: a Shipment document in ERPNext
		quote_response: API response after calling get_ltl_quotes
		is_spot_quote: whether the requested quotes were spot quotes or regular quotes
		intro_text: note if spot quotes were requested but aren't supported

		Returns:
		Message string to display in UI or throws an error if encountered
		"""
		type_of_quote = "Spot Quotes" if is_spot_quote else "Quotes"
		display_message = (
			f"{intro_text}A total of {len(quote_response)} {type_of_quote} received:<br><br><ul>"
		)
		quote_summary_format = "<li>{link}: {carrier} ({scac}) - {svc} service level for {currency}{amount}. Est. delivery in {est_days} days</li>"
		quote_summaries = []

		for quote in quote_response:
			sq = frappe.new_doc("Shipment Quotation")
			sq.shipment = doc.name
			sq.carrier = doc.preferred_carrier
			sq.carrier_id = doc.carrier_id
			sq.carrier_scac = frappe.get_value("Supplier", doc.preferred_carrier, "ltl_carrier_scac")
			sq.is_spot_quote = is_spot_quote
			sq.quote_or_offer_id = quote.get("quote_id")
			sq.expiration_date = quote.get("expiration_date")
			sq.pickup_date = quote.get("pickup_date")
			sq.service_level = quote.get("service", {}).get("carrier_description")
			sq.estimated_delivery_days = float(quote.get("estimated_delivery_days", 0))
			backup_total = 0.0
			for charge in quote.get("charges", []):
				c_type = charge.get("type", "N/A").title()
				amount = float(charge.get("amount", {}).get("value", 0))
				currency = charge.get("amount", {}).get("currency")
				if c_type.lower() == "total":
					sq.grand_total = amount
					continue  # don't save total into table
				if c_type.lower() == "discount":
					amount *= -1
				backup_total += amount
				sq.append(
					"charges",
					{
						"type": c_type,
						"amount": amount,
						"currency": currency,
						"description": charge.get("description"),
					},
				)

			if not sq.grand_total:
				sq.grand_total = backup_total
			sq.save()
			quote_summaries.append(
				quote_summary_format.format(
					link=get_link_to_form(sq.doctype, sq.name),
					carrier=sq.carrier,
					scac=sq.carrier_scac or "N/A",
					svc=sq.service_level or "N/A",
					currency=currency,
					amount=sq.grand_total,
					est_days=sq.estimated_delivery_days or "N/A",
				)
			)

		display_message += "".join(quote_summaries) + "</ul>"
		return display_message

	def supports_scheduled_pickup(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""
		Convenience function that returns dict with True/False whether a specific carrier (or the
		API in general) supports electronically scheduling a pickup.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Dict with key for "supports_pickup" with boolean value
		"""
		default = {"supports_pickup": False}
		if not doc.preferred_carrier:
			return default
		carrier_id = doc.carrier_id or self.get_carrier_id_for_supplier(
			doc.preferred_carrier, settings_name, get_shipment_company_for_ltl(doc)
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting whether LTL carrier supports electronically scheduling a pickup",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to check if supports scheduling a pickup via the API.",
			)
			return default

		feats = self.list_ltl_carrier_features(carrier_id, settings_name, doc)
		return {"supports_pickup": "scheduled_pickup" in feats}

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""
		Schedules LTL pickup with quote ID(s) saved in doc. If successful, sets fields in the
		Shipment Information section (if available in the response):
		- carrier and carrier service
		- pickup ID and shipment ID
		- the awb_number field with the tracking/PRO number
		- the shipment amount
		- attach BOL to the Shipment doc

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Message string to display in UI or throws an error if encountered
		"""

		self.validate_carrier_and_id(doc, settings_name)
		if not self.supports_scheduled_pickup(doc, settings_name).get("supports_pickup"):
			return f"{doc.preferred_carrier} does not support scheduling a pickup through the API - please contact them directly to schedule a pickup."

		pu_response = self.schedule_ltl_pickup_with_quote_id(doc, settings_name)
		dt, dn = doc.doctype, doc.name
		frappe.set_value(dt, dn, "carrier", doc.preferred_carrier)

		accepted_sq = doc.accepted_quotation or frappe.db.get_value(
			"Shipment Quotation", {"shipment": doc.name, "docstatus": 1}, "name"
		)
		if accepted_sq:
			sl = frappe.db.get_value("Shipment Quotation", accepted_sq, "service_level")
			frappe.set_value(dt, dn, "carrier_service", sl)

		frappe.set_value(dt, dn, "shipment_id", pu_response.get("shipment_id"))
		frappe.set_value(dt, dn, "pickup_id", pu_response.get("pickup_id"))
		frappe.set_value(dt, dn, "awb_number", pu_response.get("pro_number"))

		# Attach generated documents to Shipment doc
		docs_saved = False
		try:
			now_dt = now().split(".")[0]  # remove microseconds
			docs = pu_response.get("documents", [])
			for d in docs:
				d_data = base64.b64decode(d.get("image"))
				save_file(f"{doc.name}-{d.get('type')}-{now_dt}.pdf", d_data, "Shipment", doc.name)
			docs_saved = len(docs) > 0
		except Exception as e:
			frappe.log_error(
				title=f"Error Attaching {self.provider} Documents to Shipment",
				message=f"An error occurred trying to save the BOL while scheduling a pickup.\n{e}\n{frappe.get_traceback()}",
				reference_doctype="Shipment",
				reference_name=doc.name,
			)

		msg = f"Pickup successfully scheduled with ID: {pu_response.get('pickup_id')} and PRO Number: {pu_response.get('pro_number')}."
		if docs_saved:
			msg += "<br><br>There were automatically generated documents that have been saved and attached."
		return msg

	##### CLASS HELPER FUNCTIONS TO SUPPORT BASE CLASS FUNCTIONALITY #####
	DEFAULT_BASE_URL = "https://api.shipengine.com"

	def ltl_auth_doc(
		self,
		doc: Shipment | None = None,
		company: str | None = None,
		supplier: str | None = None,
	):
		"""Build a minimal doc-like object for resolving Freight Carrier Settings (company + supplier)."""
		if doc is not None:
			return doc
		if company and supplier:
			return frappe._dict(
				pickup_from_type="Company",
				pickup_company=company,
				preferred_carrier=supplier,
			)
		return None

	def get_base_url_and_headers(self, doc: Shipment | None = None) -> tuple[str, dict]:
		"""Resolve ShipEngine base URL and Api-Key from Freight Carrier Settings only."""
		if doc is None:
			frappe.throw(
				_(
					"LTL requires Freight Carrier Settings for the shipment's company and preferred carrier "
					"(or explicit company and supplier)."
				)
			)
		default_base = self.DEFAULT_BASE_URL
		co = get_shipment_company_for_ltl(doc)
		supplier = getattr(doc, "preferred_carrier", None)
		if not co or not supplier:
			frappe.throw(
				_(
					"Set Preferred Carrier and company (pickup or delivery as Company) on the Shipment "
					"so LTL can load Freight Carrier Settings."
				)
			)
		fc = get_freight_carrier_settings(co, supplier)
		if not fc:
			frappe.throw(
				_(
					"No Freight Carrier Settings for company {0} and supplier {1}. "
					"Save Shipstation Settings with ShipStation API v2 enabled to sync keys into Freight Carrier Settings, "
					"or create a Freight Carrier Settings record manually."
				).format(co, supplier)
			)
		assert fc is not None
		api_key = fc.get_password("ltl_api_key")
		if not api_key:
			frappe.throw(
				_(
					"LTL API Key is missing on Freight Carrier Settings {0}. "
					"Sync from Shipstation Settings (API v2) or enter the key on that document."
				).format(fc.name)
			)
		base_url = (fc.base_url or default_base).rstrip("/")
		headers = {"Content-Type": "application/json", "Accept": "application/json", "Api-Key": api_key}
		return base_url, headers

	def get_api_response_error_info(self, response_json):
		"""
		Extracts error type and message from Shipstation API response JSON.

		Args:
		response_json: JSON object returned from API (response.json())
		"""
		err_type = "error type not provided"
		err_msg = "error message not provided"
		if isinstance(response_json, dict):
			errors = response_json.get("errors", [{}])[0]
			err_type = errors.get("error_type", err_type)
			err_msg = errors.get("message", err_msg)
		return err_type, err_msg

	def format_ltl_carrier(self, carrier) -> dict:
		"""Format LTL carrier data for consistent output."""
		carrier["carrier_code"] = carrier.get("scac")
		for p in carrier.get("packages", []):
			p["package_features"] = ", ".join(p.get("features", []))

		return carrier

	def validate_carrier_and_id(self, doc: Shipment, settings_name: str | None = None) -> None:
		"""
		Validates that doc has a preferred carrier set and system can find a carrier ID for them.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		None (throws error as-needed)
		"""
		if not doc.preferred_carrier:
			frappe.throw(
				f"{self.provider} requires a carrier for this function - please set the Preferred Carrier field."
			)

		carrier_id = doc.carrier_id or self.get_carrier_id_for_supplier(
			doc.preferred_carrier, settings_name, get_shipment_company_for_ltl(doc)
		)
		if not carrier_id:
			frappe.throw(
				f"No {self.provider} carrier ID found for the preferred carrier — set Supplier LTL Carrier ID, "
				"fetch LTL carriers using Freight Carrier Settings on Shipstation Settings, or sync carrier metadata."
			)
		return

	def get_ltl_carrier(
		self, carrier_id: str, settings_name: str | None = None, doc: Shipment | None = None
	) -> dict:
		"""
		Get details for a specific LTL carrier.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name
		doc: Optional Shipment for Freight Carrier Settings API key resolution

		Returns:
		LTL carrier details dict
		"""
		base_url, headers = self.get_base_url_and_headers(doc)

		data: dict = {}
		try:
			with httpx.Client() as client:
				response = client.get(
					f"{base_url}/v-beta/ltl/carriers/{carrier_id}",
					headers=headers,
				)
				data = response.json()
				response.raise_for_status()
				return self.format_ltl_carrier(data)
		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error getting LTL carrier",
				message=f"Carrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to get LTL carrier - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return {}

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting carrier", message=error_msg)
			frappe.throw(_("Failed to get carrier: {0}").format(error_msg))
			return {}

	def list_ltl_carrier_accessorial_services(
		self, carrier_id: str, settings_name: str | None = None, doc: Shipment | None = None
	) -> list[dict]:
		"""
		List all options aka accessorial services (e.g. Hazardous Material, Perishable, Inside Pickup)
		for a specific LTL carrier.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of LTL carrier options dicts with attributes, code, features, and name for each option
		"""
		base_url, headers = self.get_base_url_and_headers(doc)

		data: dict = {}
		try:
			with httpx.Client() as client:
				response = client.get(
					f"{base_url}/v-beta/ltl/carriers/{carrier_id}/options",
					headers=headers,
				)
				data = response.json()
				response.raise_for_status()
				return data.get("options", [])

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error listing LTL carrier options",
				message=f"Carrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to list LTL carrier options - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return []

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier options", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier options: {0}").format(error_msg))
			return []

	def list_ltl_carrier_features(
		self, carrier_id: str, settings_name: str | None = None, doc: Shipment | None = None
	) -> list[str]:
		"""
		Convenience function to list all features (e.g. "spot_quote", "tracking", "scheduled_pickup")
		for a specific LTL carrier. This retrieves the "features" key from the carrier data returned
		using the carrier ID vs calling the endpoint to get features based on the carrier code (SCAC).

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of feature strings
		"""
		carrier_data = self.get_ltl_carrier(carrier_id=carrier_id, settings_name=settings_name, doc=doc)
		return carrier_data.get("features", [])

	def list_ltl_carrier_package_types(
		self, carrier_id: str, settings_name: str | None = None, doc: Shipment | None = None
	) -> list[dict]:
		"""
		List all package aka container types (e.g. "Bag", "Skid", or "Piece") for a specific LTL
		carrier.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of LTL carrier package dicts with code, features, and name for each package
		"""
		base_url, headers = self.get_base_url_and_headers(doc)

		data: dict = {}
		try:
			with httpx.Client() as client:
				response = client.get(
					f"{base_url}/v-beta/ltl/carriers/{carrier_id}/packages",
					headers=headers,
				)
				data = response.json()
				response.raise_for_status()
				return data.get("packages", [])

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error listing LTL carrier package/container types",
				message=f"Carrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_(
					"Failed to list LTL carrier package/container types - error type: {0}, message: {1}, error: {2}"
				).format(err_type, err_msg, str(e))
			)
			return []

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier package/container types", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier package/container types: {0}").format(error_msg))
			return []

	def get_all_ltl_carrier_package_types(
		self,
		settings_name: str | None = None,
		company: str | None = None,
		supplier: str | None = None,
		doc: Shipment | None = None,
	) -> dict:
		"""
		Get package types for all configured LTL carriers.

		Args:
		settings_name: Optional Shipstation Settings document name (for cached LTL JSON only)
		company: Company for Freight Carrier Settings when doc is not passed
		supplier: Supplier for Freight Carrier Settings when doc is not passed
		doc: Optional Shipment for auth context

		Returns:
		Dict mapping carrier_id to list of package types
		"""
		auth = self.ltl_auth_doc(doc=doc, company=company, supplier=supplier)
		if auth is None:
			frappe.throw(
				_(
					"Company and supplier (or a Shipment with Preferred Carrier) are required for LTL package types."
				)
			)
			return {}

		settings = get_shipstation_settings_optional(settings_name)

		# Get carrier IDs from stored LTL carrier data
		carrier_data = []
		if settings and settings.shipstation_api_ltl_carrier_data:
			carrier_data = json.loads(settings.shipstation_api_ltl_carrier_data)

		if not carrier_data:
			co = get_shipment_company_for_ltl(auth)
			sup = auth.get("preferred_carrier")
			carriers = self.list_ltl_carriers(company=co, supplier=sup)
			carrier_ids = [c["carrier_id"] for c in carriers]
		else:
			carrier_ids = [c.get("carrier_id") for c in carrier_data if c.get("carrier_id")]

		result = {}
		for carrier_id in carrier_ids:
			try:
				packages = self.list_ltl_carrier_package_types(carrier_id, doc=auth)
				result[carrier_id] = packages
			except Exception as e:
				# Log but don't fail for individual carriers
				frappe.log_error(
					title=f"Error fetching packages for LTL carrier {carrier_id}",
					message=str(e),
				)
				result[carrier_id] = []

		return result

	def list_ltl_carrier_services(
		self, carrier_id: str, settings_name: str | None = None, doc: Shipment | None = None
	) -> list[dict]:
		"""
		List all service levels (e.g. Guaranteed Morning, Guaranteed Noon, Standard) for a specific
		LTL carrier.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of LTL carrier service dicts with code, features, and name for each option
		"""
		base_url, headers = self.get_base_url_and_headers(doc)

		data: dict = {}
		try:
			with httpx.Client() as client:
				response = client.get(
					f"{base_url}/v-beta/ltl/carriers/{carrier_id}/services",
					headers=headers,
				)
				data = response.json()
				response.raise_for_status()
				return data.get("services", [])

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error listing LTL carrier options",
				message=f"Carrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to list LTL carrier options - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return []

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier options", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier options: {0}").format(error_msg))
			return []

	def resolve_package_type_code(
		self, doc, carrier_id: str, settings_name: str | None = None
	) -> None:
		"""
		Ensure doc.package_type_code is set before building the LTL payload.
		If not already populated, fetches the carrier's available package types and
		selects the one whose name matches doc.package_type (case-insensitive), falling
		back to the first option returned by the API.
		Sets the resolved code directly on the doc object (in-memory only).
		"""
		if doc.get("package_type_code"):
			return

		pkg_types = self.list_ltl_carrier_package_types(carrier_id, settings_name, doc)
		if not pkg_types:
			frappe.throw(
				_(
					"Could not retrieve package type options from the carrier. "
					"Please set Package Type Code manually on the Shipment."
				),
				title=_("Package Type Required"),
			)
			return

		preferred_name = (doc.get("package_type") or "").strip().lower()
		default = ([p for p in pkg_types if p.get("name") == "Package"] or pkg_types)[0]
		matched = next(
			(p for p in pkg_types if p.get("name", "").strip().lower() == preferred_name),
			None,
		)
		doc.package_type_code = (matched or default).get("code")

	def validate_billing(self, doc) -> None:
		"""
		Raise a clear ValidationError before the API call if billing fields required
		by ShipEngine LTL are missing.  Called outside the try/except in
		request_ltl_quote and request_ltl_spot_quote so the message reaches the user
		directly rather than being wrapped in a generic error.

		ShipEngine LTL always requires bill_to.account — it identifies the shipper or
		payer account with the carrier.  'Third Party' payment terms additionally require
		it on behalf of the third party.
		"""
		if not doc.get("billing_account"):
			frappe.throw(
				_(
					"A Billing Account Number is required for LTL quotes. "
					"Please enter your carrier account number in the Billing Account field on the Shipment."
				),
				title=_("Billing Account Required"),
			)
			return

	def request_ltl_quote(
		self, carrier_id: str, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		"""
		Gets LTL quote given a specific LTL carrier and shipment data.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		doc: a Shipment document in ERPNext from which to retrieve the shipment info
		settings_name: Optional Shipstation Settings document name

		Returns:
		List containing the ShipEngine quote dict, includes quote_id, charges list of dicts, and
		shipment info
		"""
		self.resolve_package_type_code(doc, carrier_id, settings_name)
		self.validate_billing(doc)
		base_url, headers = self.get_base_url_and_headers(doc)

		data: dict = {}
		try:
			with httpx.Client() as client:
				payload = {
					"carrier_id": carrier_id,
					"shipment": self.get_shipment_object_from_doc(doc),
					"shipment_measurements": self.get_shipment_measurements_object_from_doc(doc),
				}
				response = client.post(
					f"{base_url}/v-beta/ltl/quotes/{carrier_id}", headers=headers, json=payload
				)
				data = response.json()
				response.raise_for_status()
				data["estimated_delivery_date"] = str(
					add_days(doc.pickup_date, data.get("estimated_delivery_days", 1))
				)
				return [data]

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error getting LTL quote",
				message=f"Document: {doc.name}\nCarrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to get LTL quote - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return []

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting LTL quote", message=error_msg)
			frappe.throw(_("Failed to get LTL quote: {0}").format(error_msg))
			return []

	def request_ltl_spot_quote(
		self, carrier_id: str, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		"""
		Gets LTL spot quote given a specific LTL carrier and shipment data.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		doc: a Shipment document in ERPNext from which to retrieve the shipment info
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of ShipEngine spot quote dicts
		"""
		self.resolve_package_type_code(doc, carrier_id, settings_name)
		self.validate_billing(doc)
		base_url, headers = self.get_base_url_and_headers(doc)

		data: dict = {}
		try:
			with httpx.Client() as client:
				payload = {
					"carrier_id": carrier_id,
					"shipment": self.get_shipment_object_from_doc(doc),
					"shipment_measurements": self.get_shipment_measurements_object_from_doc(doc),
				}
				response = client.post(
					f"{base_url}/v-beta/ltl/spot-quotes/{carrier_id}", headers=headers, json=payload
				)
				data = response.json()
				response.raise_for_status()
				quotes = data.get("quotes", [])
				for q in quotes:
					q["estimated_delivery_date"] = str(
						add_days(doc.pickup_date, q.get("estimated_delivery_days", 1))
					)
				return quotes

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error getting LTL spot quote",
				message=f"Document: {doc.name}\nCarrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to get LTL spot quote - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return []

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting LTL spot quote", message=error_msg)
			frappe.throw(_("Failed to get LTL spot quote: {0}").format(error_msg))
			return []

	def schedule_ltl_pickup_with_quote_id(
		self, doc: Shipment, settings_name: str | None = None
	) -> dict:
		"""
		Schedules an LTL pickup using a ShipEngine quote ID (may be a quote or spot quote ID).

		Args:
		doc: a Shipment document in ERPNext from which to retrieve the shipment info
		settings_name: Optional Shipstation Settings document name

		Returns:
		ShipEngine LTL scheduled pickup dict, includes confirmation_number, pro_number, documents
		(Base64-encoded BOL, which decodes to PDF format), pickup_id, and shipment_id among other
		information
		"""
		base_url, headers = self.get_base_url_and_headers(doc)
		quote_id = doc.quote_or_offer_id
		if not quote_id:
			frappe.throw(
				f"{self.provider} requires a Quote ID to schedule an LTL pickup. Get LTL quotes then submit one of the generated Shipment Quotations to accept it."
			)
			return {}

		delivery_date = doc.get("estimated_delivery_date") or frappe.get_value(
			"Shipment Quotation", doc.accepted_quotation, "estimated_delivery_date"
		)
		if not doc.get("estimated_delivery_date"):
			frappe.set_value(doc.doctype, doc.name, "estimated_delivery_date", delivery_date)

		data: dict = {}
		try:
			with httpx.Client() as client:
				payload = {
					"quote_id": quote_id,
					"pickup_date": str(doc.get("pickup_date")),  # YYYY-MM-DD format
					"pickup_window": self.get_shipment_pickup_window_from_doc(doc),
					"delivery_date": str(delivery_date),  # YYYY-MM-DD format
					"carrier": {  # optional
						"instructions": doc.get("carrier_instructions", ""),
						"test": False,  # whether or not this is a test call
					},
					# "options": [],  # optional / were included in the quote
				}
				response = client.post(
					f"{base_url}/v-beta/ltl/quotes/{quote_id}/pickup", headers=headers, json=payload
				)
				data = response.json()
				response.raise_for_status()
				return data

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error scheduling LTL pickup with quote ID",
				message=f"Document: {doc.name}\nQuote ID: {quote_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_(
					"Failed to schedule LTL pickup with quote ID - error type: {0}, message: {1}, error: {2}"
				).format(err_type, err_msg, str(e))
			)
			return {}

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error scheduling LTL pickup with quote ID", message=error_msg)
			frappe.throw(_("Failed to schedule LTL pickup with quote ID: {0}").format(error_msg))
			return {}

	def schedule_ltl_pickup_without_quote_id(
		self, doc: Shipment, settings_name: str | None = None
	) -> dict:
		"""
		Schedules an LTL pickup without a quote ID with a specific LTL carrier and shipment data.

		Args:
		doc: a Shipment document in ERPNext from which to retrieve the shipment info
		settings_name: Optional Shipstation Settings document name

		Returns:
		ShipEngine LTL scheduled pickup dict, includes confirmation_number, pro_number, documents
		(Base64-encoded BOL, which decodes to PDF format), pickup_id, and shipment_id among other
		information
		"""
		base_url, headers = self.get_base_url_and_headers(doc)
		self.validate_carrier_and_id(doc, settings_name)
		carrier_id = doc.carrier_id

		data: dict = {}
		try:
			shipment_object = self.get_shipment_object_from_doc(doc=doc, for_pickup_no_quote=True)
			with httpx.Client() as client:
				payload = {
					"carrier_id": carrier_id,
					"carrier": {  # optional
						"instructions": doc.get("carrier_instructions", ""),
						"test": False,  # whether or not this is a test call
					},
					"options": shipment_object.get("options"),  # accessorial services
					"shipment": shipment_object,
				}
				if doc.get("quote_or_offer_id"):
					# type may be "bill_of_lading", "pro", "quote", "purchase_order", or "other"
					payload.update(
						{"reference_identifiers": [{"type": "quote", "value": doc.get("quote_or_offer_id")}]}
					)
				response = client.post(
					f"{base_url}/v-beta/ltl/pickups/{carrier_id}", headers=headers, json=payload
				)
				data = response.json()
				response.raise_for_status()
				return data

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error scheduling LTL pickup",
				message=f"Document: {doc.name}\nCarrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to schedule LTL pickup - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return {}

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error scheduling LTL pickup", message=error_msg)
			frappe.throw(_("Failed to schedule LTL pickup: {0}").format(error_msg))
			return {}

	def get_bol_with_quote_id(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""
		Gets the Bill of Lading (BOL) for a scheduled pickup using the ShipEngine quote/spot
		quote ID and attaches it to the Shipment doc.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		ShipEngine dict with type (value will be "bill_of_lading"), image (value is the base64-
		encoded BOL document), and format (value will be "pdf") for the shipment
		"""
		base_url, headers = self.get_base_url_and_headers(doc)
		quote_id = doc.quote_or_offer_id
		if not quote_id:
			frappe.throw(
				f"No {self.provider} Quote ID found - make sure to get LTL quotes then accept one of the generated Supplier Quotations."
			)
			return {}

		data: dict = {}
		try:
			with httpx.Client() as client:
				payload = {"carrier_instructions": doc.get("carrier_instructions", "")}
				response = client.post(
					f"{base_url}/v-beta/ltl/quote/{quote_id}/bill_of_lading",
					headers=headers,
					json=payload,
				)
				data = response.json()
				response.raise_for_status()
				docs = data.get("documents", [])
				now_dt = now().split(".")[0]  # remove microseconds
				for d in docs:
					if d.get("type") == "bill_of_lading":
						bol = base64.b64decode(d.get("image"))
						save_file(f"{doc.name}-{d.get('type')}-{now_dt}.pdf", bol, "Shipment", doc.name)
				return data

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error getting BOL using quote ID",
				message=f"Quote ID: {quote_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to get BOL using quote ID - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return {}

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting BOL using quote ID", message=error_msg)
			frappe.throw(_("Failed to get BOL using quote ID: {0}").format(error_msg))
			return {}

	def get_bol_with_pickup_id(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""
		Gets the Bill of Lading (BOL) for a scheduled pickup using the ShipEngine pickup ID and
		attaches it to the Shipment doc.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		ShipEngine dict with type (value will be "bill_of_lading"), image (value is the base64-encoded
		BOL document), and format (value will be "pdf") for the shipment
		"""
		base_url, headers = self.get_base_url_and_headers(doc)
		pickup_id = doc.pickup_id
		if not pickup_id:
			frappe.throw(
				f"No {self.provider} Pickup ID found - make sure to first schedule an LTL pickup to generate this ID."
			)
			return {}

		data: dict = {}
		try:
			with httpx.Client() as client:
				payload = {"carrier_instructions": doc.get("carrier_instructions", "")}
				response = client.post(
					f"{base_url}/v-beta/ltl/pickups/{pickup_id}/bill_of_lading",
					headers=headers,
					json=payload,
				)
				data = response.json()
				response.raise_for_status()
				docs = data.get("documents", [])
				now_dt = now().split(".")[0]  # remove microseconds
				for d in docs:
					if d.get("type") == "bill_of_lading":
						bol = base64.b64decode(d.get("image"))
						save_file(f"{doc.name}-{d.get('type')}-{now_dt}.pdf", bol, "Shipment", doc.name)
				return data

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error getting BOL using pickup ID",
				message=f"Pickup ID: {pickup_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to get BOL using pickup ID - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return {}

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting BOL using pickup ID", message=error_msg)
			frappe.throw(_("Failed to get BOL using pickup ID: {0}").format(error_msg))
			return {}

	def list_ltl_carrier_documents_by_id_and_pronumber(
		self, doc: Shipment, settings_name: str | None = None
	) -> list[dict]:
		"""
		List all documents from an LTL carrier associated with a PRO number. The documents that
		can be associated with a PRO number are "bill_of_lading", "delivery_receipt", "invoice",
		or "weight_inspection_certificate".

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		List of LTL carrier document dicts, each with the type (options noted above), image
		(base64-encoded bill of lading document), and format (always PDF for pickup responses,
		the format the image will be in once decoded)
		"""
		self.validate_carrier_and_id(doc, settings_name)
		base_url, headers = self.get_base_url_and_headers(doc)
		carrier_id = doc.carrier_id
		pro_number = doc.awb_number
		if not pro_number:
			frappe.throw("No PRO Number found - make sure to schedule an LTL pickup first.")
			return []

		data: dict = {}
		try:
			with httpx.Client() as client:
				response = client.get(
					f"{base_url}/v-beta/ltl/carriers/{carrier_id}/documents/{pro_number}",
					headers=headers,
				)
				data = response.json()
				response.raise_for_status()
				return data.get("documents", [])

		except httpx.HTTPStatusError as e:
			err_type, err_msg = self.get_api_response_error_info(data)
			frappe.log_error(
				title="Error listing LTL carrier documents",
				message=f"Carrier ID: {carrier_id}\nError Type: {err_type}\nError Message: {err_msg}\nResponse: {e.response.text}",
			)
			frappe.throw(
				_("Failed to list LTL carrier documents - error type: {0}, message: {1}, error: {2}").format(
					err_type, err_msg, str(e)
				)
			)
			return []

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier documents", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier documents: {0}").format(error_msg))
			return []

	# Conversion factors to pounds and cubic feet for density calculation.
	# Built from WEIGHT_UOM_MAP / DIMENSION_UOM_MAP so all aliases (Kg, kg, cm, …) are covered.
	# Note: the intermediate dicts are module-level (CANONICAL_TO_LB / CANONICAL_TO_CUFT)
	# because Python class-body dict comprehensions can't reference sibling class variables.
	WEIGHT_TO_LB = {k: CANONICAL_TO_LB[v] for k, v in WEIGHT_UOM_MAP.items() if v in CANONICAL_TO_LB}
	VOL_TO_CUFT = {
		k: CANONICAL_TO_CUFT[v] for k, v in DIMENSION_UOM_MAP.items() if v in CANONICAL_TO_CUFT
	}

	# NMFC density → freight class mapping (density in lb/ft³)
	DENSITY_TO_CLASS = [
		(50, 50),
		(35, 55),
		(30, 60),
		(22.5, 65),
		(15, 70),
		(13.5, 77.5),
		(12, 85),
		(10.5, 92.5),
		(9, 100),
		(8, 110),
		(7, 125),
		(6, 150),
		(5, 175),
		(4, 200),
		(3, 250),
		(2, 300),
		(1, 400),
	]

	def density_to_freight_class(self, density_lb_ft3: float) -> float:
		"""Return the NMFC freight class for the given density in lb/ft³."""
		for min_density, freight_class in self.DENSITY_TO_CLASS:
			if density_lb_ft3 >= min_density:
				return freight_class
		return 500

	def get_dn_item_weight(self, dn_detail: str) -> tuple[float, str]:
		"""
		Return (total_weight, weight_uom) for a Delivery Note Item row.

		Falls back to (0.0, "Pound") if the row is not found or has no weight.
		"""
		if not dn_detail:
			return 0.0, "Pound"
		row = frappe.db.get_value(
			"Delivery Note Item",
			dn_detail,
			["weight_per_unit", "total_weight", "qty", "weight_uom"],
			as_dict=True,
		)
		if not row:
			return 0.0, "Pound"
		weight = flt(row.total_weight) or flt(row.weight_per_unit) * flt(row.qty)
		uom = row.weight_uom or "Pound"
		return weight, uom

	def build_packages_from_sdn(self, doc) -> list[dict]:
		"""
		Build a ShipEngine LTL packages list from Shipment Delivery Note rows.

		Groups SDN rows by parcel_number.  For each parcel:
		  - Dimensions come from the first SDN row that has non-zero parcel dimensions.
		  - Weight is the sum of each row's parcel_weight (falling back to the linked
		    DN item's total_weight when parcel_weight is zero).
		  - Density is calculated automatically from dimensions and weight.
		  - Freight class is derived from density unless explicitly set on the Shipment.

		Returns a list of package dicts suitable for a ShipEngine LTL quote payload.
		Raises ValidationError if SDN has no rows or none have a parcel_number.
		"""
		# sdn_rows = [frappe._dict(r) for r in (doc.shipment_delivery_note or [])]
		sdn_rows = doc.shipment_delivery_note
		packed = [r for r in sdn_rows if r.parcel_number]
		if not packed:
			frappe.throw(
				_(
					"Shipment Delivery Note items must be assigned to parcels (set Parcel #) before requesting an LTL quote."
				),
				title=_("No Packed Items"),
			)

		# Group rows by parcel_number
		parcels: dict[int, list] = {}
		for row in packed:
			parcels.setdefault(int(row.parcel_number), []).append(row)

		packages = []
		for parcel_num in sorted(parcels):
			rows = parcels[parcel_num]

			# Dimensions: first row with all non-zero values wins
			dim_row = next(
				(r for r in rows if flt(r.parcel_length) and flt(r.parcel_width) and flt(r.parcel_height)),
				None,
			)
			if dim_row is None:
				frappe.throw(
					_(
						"Parcel {0} has no dimensions set. Please enter length, width, and height on at least one Shipment Delivery Note row for this parcel before requesting an LTL quote."
					).format(parcel_num),
					title=_("Missing Parcel Dimensions"),
				)
			assert dim_row is not None
			length = flt(dim_row.parcel_length)
			width = flt(dim_row.parcel_width)
			height = flt(dim_row.parcel_height)
			dim_uom_key = dim_row.dimension_uom or "Inch"
			len_uom = self.uom_map["length"].get(dim_uom_key)
			if not len_uom:
				frappe.throw(
					_(
						f"Unsupported dimension UOM '{dim_uom_key}' on parcel {parcel_num}. Use {comma_or(list(self.uom_map['length'].keys()))}."
					)
				)

			# Weight: prefer explicit parcel_weight; fall back to DN item weight
			total_weight = 0.0
			weight_uom_key = "Pound"
			for row in rows:
				pw = flt(row.parcel_weight)
				if pw:
					total_weight += pw
					weight_uom_key = row.parcel_weight_uom or "Pound"
				elif row.dn_detail:
					w, wu = self.get_dn_item_weight(row.dn_detail)
					total_weight += w
					weight_uom_key = wu or weight_uom_key

			if not total_weight:
				frappe.throw(
					_(
						"Parcel {0} has no weight. Please set parcel weight on the Shipment Delivery Note rows, or ensure the linked Delivery Note items have a weight defined."
					).format(parcel_num),
					title=_("Missing Parcel Weight"),
				)

			weight_uom = self.uom_map["weight"].get(weight_uom_key)
			if not weight_uom:
				frappe.throw(
					_(
						f"Unsupported weight UOM '{weight_uom_key}' on parcel {parcel_num}. Use {comma_or(list(self.uom_map['weight'].keys()))}."
					)
				)

			# Density and freight class (auto-calculated)
			density_parcel = frappe._dict(
				{
					"length": length,
					"width": width,
					"height": height,
					"length_uom": dim_uom_key,
					"weight": total_weight,
					"weight_uom": weight_uom_key,
					"count": 1,
				}
			)
			density_lb_ft3 = self.calculate_density_lb_ft3(density_parcel)
			freight_class = self.density_to_freight_class(density_lb_ft3)

			pkg: dict = {
				"code": doc.package_type_code,
				"freight_class": freight_class,
				"density": {"value": round(density_lb_ft3, 4), "unit": "lb/ft3"},
				"description": doc.get("description_of_content", ""),
				"dimensions": {"width": width, "height": height, "length": length, "unit": len_uom},
				"weight": {"value": total_weight, "unit": weight_uom},
				"quantity": 1,
				"stackable": False,
				"hazardous_materials": bool(doc.get("hazardous_material")),
			}
			if doc.get("nmfc_freight_class"):
				pkg["nmfc_code"] = str(doc.nmfc_freight_class)
			packages.append(pkg)

		return packages

	def calculate_density_lb_ft3(self, row: ShipmentParcel | dict) -> float:
		"""
		Calculate density in lb/ft³ from a Shipment Parcel row's dimensions and weight.

		Uses length/width/height with length_uom and weight with weight_uom.
		Returns 0.0 if any required field is missing or zero.
		"""
		length = flt(row.get("length") if isinstance(row, dict) else row.length)
		width = flt(row.get("width") if isinstance(row, dict) else row.width)
		height = flt(row.get("height") if isinstance(row, dict) else row.height)
		weight = flt(row.get("weight") if isinstance(row, dict) else row.weight)
		count = flt(row.get("count") if isinstance(row, dict) else getattr(row, "count", 1)) or 1

		len_uom = row.get("length_uom") if isinstance(row, dict) else getattr(row, "length_uom", None)
		wt_uom = row.get("weight_uom") if isinstance(row, dict) else getattr(row, "weight_uom", None)

		if not (length and width and height and weight and len_uom and wt_uom):
			return 0.0

		vol_to_cuft = self.VOL_TO_CUFT.get(len_uom)
		wt_to_lb = self.WEIGHT_TO_LB.get(wt_uom)
		if not vol_to_cuft or not wt_to_lb:
			return 0.0

		volume_cuft = length * width * height * vol_to_cuft * count
		weight_lb = weight * wt_to_lb
		return round(weight_lb / volume_cuft, 4) if volume_cuft else 0.0

	def get_and_validate_uoms(self, row: ShipmentParcel | dict) -> tuple | None:
		"""
		Given a Shipment Parcel row from a Shipment doc, validates the UOMs for length, width, and
		density, then returns the appropriate values to use for each in a Shipstation API call.

		If density_uom is not set, defaults to "Pound/Cubic Foot" (the only UOM supported by
		Shipstation).  The corresponding density value is calculated automatically from the parcel
		dimensions and weight when density is zero or unset.

		Args:
		row: a Shipment Parcel document (a row in a Shipment's Shipment Parcel child table)

		Returns:
		The value to use for the UOM in the API call, or an Error if Shipstation doesn't accept
		the given UOM.
		"""
		errors = []
		err_template = "The {dim} UOM of {user_uom} used in row {row_idx} is not supported by Shipstation. Please use {supported}."
		if isinstance(row, dict):
			length_uom_val = row.get("length_uom")
			weight_uom_val = row.get("weight_uom")
			density_uom_val = row.get("density_uom")
			row_idx = row.get("idx", "")
		else:
			length_uom_val = row.length_uom
			weight_uom_val = row.weight_uom
			density_uom_val = row.density_uom
			row_idx = row.idx

		len_uom = self.uom_map["length"].get(length_uom_val)
		if not len_uom:
			supported_lengths = list(self.uom_map["length"].keys())
			errors.append(
				err_template.format(
					dim="length",
					user_uom=length_uom_val,
					row_idx=row_idx,
					supported=comma_or(supported_lengths),
				)
			)

		weight_uom = self.uom_map["weight"].get(weight_uom_val)
		if not weight_uom:
			supported_weights = list(self.uom_map["weight"].keys())
			errors.append(
				err_template.format(
					dim="weight",
					user_uom=weight_uom_val,
					row_idx=row_idx,
					supported=comma_or(supported_weights),
				)
			)

		# Density UOM defaults to Pound/Cubic Foot — the only UOM Shipstation accepts.
		# Explicit validation is preserved for any non-empty value so typos surface clearly.
		effective_density_uom = density_uom_val or "Pound/Cubic Foot"
		density_uom = self.uom_map["density"].get(effective_density_uom)
		if not density_uom:
			supported_densities = list(self.uom_map["density"].keys())
			errors.append(
				err_template.format(
					dim="density",
					user_uom=density_uom_val,
					row_idx=row_idx,
					supported=comma_or(supported_densities),
				)
			)

		if errors:
			frappe.throw(msg=". ".join(errors))
		assert len_uom is not None and weight_uom is not None and density_uom is not None
		return len_uom, weight_uom, density_uom

	def get_shipment_object_from_doc(self, doc: Shipment, for_pickup_no_quote: bool = False) -> dict:
		"""
		Collects necessary data from a Shipment document in ERPNext to populate a shipment object

		Args:
		doc: a Shipment document in ERPNext from which to retrieve the shipment info
		for_pickup_no_quote: if shipment object will be used to directly schedule a pickup without
		first requesting a quote/spot quote. If True, the object includes additional pickup fields

		Returns:
		shipment object dict that may be used to get a quote, spot quote or schedule a pickup
		"""
		# Ship From and Ship To
		ship_from = self.get_address_and_contact_info(doc=doc, ship_from=True)
		if doc.billing_type == "Shipper" and doc.billing_account:
			ship_from.update({"account": doc.billing_account})
		ship_to = self.get_address_and_contact_info(doc=doc, ship_from=False)
		if doc.billing_type == "Consignee" and doc.billing_account:
			ship_to.update({"account": doc.billing_account})

		# Packages / Handling Units — built from Shipment Delivery Note rows
		haz_name = ship_from["contact"]["name"]
		haz_phone = ship_from["contact"]["phone_number"]
		packages = self.build_packages_from_sdn(doc)

		# Accessorial services (list of: {"code": "ipu", "attributes": {}}, attributes depend on svc)
		options = []
		for field, code in self.accessorial_services_map.items():
			if doc.get(field):
				svc = {"code": code}
				# TODO: what other services have required attributes?
				if code == "HAZ":
					svc.update({"attributes": {"name": haz_name, "phone": haz_phone}})
				options.append(svc)

		# Billing
		b_type = (doc.billing_type or "Shipper").replace(" ", "_").lower()
		b_pmt_terms = (doc.payment_terms or "Prepaid").replace(" ", "_").lower()
		if b_type not in ["consignee", "shipper", "third_party"]:
			frappe.throw("The Billing Type must be either 'Consignee', 'Shipper', or 'Third Party'.")

		if b_pmt_terms not in ["collect", "prepaid", "third_party"]:
			frappe.throw("The Billing Payment Term must be either 'Collect', 'Prepaid', or 'Third Party'.")

		bill_to = {
			"type": b_type,
			"payment_terms": b_pmt_terms,
		}
		if doc.get("billing_account"):
			bill_to["account"] = doc.billing_account
		if doc.billing_type == "Shipper" and not doc.billing_address:
			bill_to["address"] = ship_from["address"]
			bill_to["contact"] = ship_from["contact"]
		elif doc.billing_type == "Consignee" and not doc.billing_address:
			bill_to["address"] = ship_to["address"]
			bill_to["contact"] = ship_to["contact"]
		else:
			billing_address = frappe.get_doc("Address", doc.billing_address)
			billing_contact = frappe.get_doc("Contact", doc.billing_contact)
			bill_to.update(
				{
					"address": {
						"company_name": frappe.get_value(
							"Dynamic Link", {"parent": billing_address.name}, "link_name"
						),
						"address_line1": billing_address.address_line1,
						"address_line2": billing_address.address_line2,
						# "address_line3": None,
						"city_locality": billing_address.city,
						"state_province": billing_address.state,
						"postal_code": billing_address.pincode,
						"country_code": (
							frappe.get_value("Country", billing_address.country, "code") or ""
						).upper(),  # ISO 3166-1 alpha-2 country code
						"residential": False,
					},
					"contact": {
						"name": billing_contact.full_name,
						"phone_number": billing_contact.phone,
						"email": billing_contact.email_id,
					},
				}
			)

		requested_by = {  # TODO: have user specify this?
			"company_name": ship_from["address"]["company_name"],
			"contact": ship_from["contact"],
		}

		shipment_object = {
			"service_code": doc.get("carrier_service_level") or "stnd",
			"pickup_date": str(doc.pickup_date),  # string in YYYY-MM-DD format
			"packages": packages,
			"options": options,
			"ship_from": ship_from,
			"ship_to": ship_to,
			"bill_to": bill_to,
			"requested_by": requested_by,
		}

		if for_pickup_no_quote:
			if not doc.estimated_delivery_date:
				frappe.throw(
					msg=_("Estimated delivery Date required to schedule a pickup without a quote."),
					title=_("Missing Delivery Date"),
				)
			shipment_object.update(
				{
					"delivery_date": doc.estimated_delivery_date,
					"pickup_window": self.get_shipment_pickup_window_from_doc(doc),
				}
			)

		return shipment_object

	def get_shipment_measurements_object_from_doc(self, doc: Shipment) -> dict:
		"""
		Derive total shipment measurements from Shipment Delivery Note rows.

		Acceptable length units are: "inches" or "centimeters"
		Acceptable weight units are: "grams", "kilograms", "ounces", or "pounds"

		Totals are computed per unique parcel_number:
		  - total_linear_length: sum of each parcel's length
		  - total_width: maximum width across all parcels
		  - total_height: maximum height across all parcels
		  - total_weight: sum of weight across all parcels

		Args:
		doc: a Shipment document in ERPNext

		Returns:
		shipment measurements dict for use in a get-quote or spot-quote request
		"""
		packages = self.build_packages_from_sdn(doc)

		# Infer UOM strings from the first package (all parcels share the same UOM within a shipment)
		first = packages[0]
		len_uom = first["dimensions"]["unit"]
		weight_uom = first["weight"]["unit"]

		total_linear_length = sum(p["dimensions"]["length"] for p in packages)
		total_width = max(p["dimensions"]["width"] for p in packages)
		total_height = max(p["dimensions"]["height"] for p in packages)
		total_weight = sum(p["weight"]["value"] for p in packages)

		return {
			"total_linear_length": {"value": total_linear_length, "unit": len_uom},
			"total_width": {"value": total_width, "unit": len_uom},
			"total_height": {"value": total_height, "unit": len_uom},
			"total_weight": {"value": total_weight, "unit": weight_uom},
		}

	def get_shipment_pickup_window_from_doc(self, doc: Shipment) -> dict:
		"""
		Collects necessary data from a Shipment document in ERPNext to populate a pickup window
		object.

		Args:
		doc: a Shipment document in ERPNext from which to retrieve the pickup info

		Returns:
		pickup window object dict that may be used in a scheduled pickup request
		"""
		# 24 Hour Format: HH:MM:SS, HH:MM:SSZ, HH:MM:SS+/-HH:MM. Split removes microseconds
		# All fields required
		return {
			"start_at": str(doc.pickup_from).split(".")[0],  # Earliest time freight will be ready
			"end_at": str(doc.pickup_to).split(".")[0],  # Latest desired pickup time
			"closing_at": str(doc.pickup_to).split(".")[0],  # When facility closes for business
		}

	def get_address_and_contact_info(self, doc: Shipment, ship_from: bool = True) -> dict:
		"""
		Returns an object with "address" and "contact" keys containing the necessary payload data for
		a Shipping Object.

		Args:
		doc: a Shipment document in ERPNext from which to retrieve the pickup info
		ship_from: if True, uses the pickup data in Shipment, if False uses the delivery data

		Returns:
		A dict with the address and contact info in the structure required for Shipstation payloads.
		"""
		party_type_field = "pickup_from_type" if ship_from else "delivery_to_type"
		party_type = doc.get(party_type_field)
		address_name_field = "pickup_address_name" if ship_from else "delivery_address_name"
		residential_flag_field = "residential_pickup" if ship_from else "residential_delivery"

		if not ship_from and doc.get("freight_type") == "LTL" and not doc.get("delivery_contact_name"):
			frappe.throw(
				_("Delivery Contact is required for LTL shipments."),
				title=_("Delivery contact required"),
			)

		party_name_field_map = {
			"Company": "pickup_company" if ship_from else "delivery_company",
			"Customer": "pickup_customer" if ship_from else "delivery_customer",
			"Supplier": "pickup_supplier" if ship_from else "delivery_supplier",
			# "Contact" has no party name field — the contact IS the addressee
		}
		company_name = (
			doc.get(party_name_field_map[party_type]) if party_type in party_name_field_map else None
		)

		if ship_from and party_type == "Company":
			contact_field = "pickup_contact_person"
			contact_dt = "User"
			email_field = "email"
		elif not ship_from:
			contact_field = "delivery_contact_name"
			contact_dt = "Contact"
			email_field = "email_id"
		else:
			contact_field = "pickup_contact_name"
			contact_dt = "Contact"
			email_field = "email_id"

		address = frappe.get_doc("Address", doc.get(address_name_field))
		contact = frappe.get_doc(contact_dt, doc.get(contact_field))

		side = _("pickup") if ship_from else _("delivery")
		phone = contact.phone or address.phone
		if not phone:
			frappe.throw(
				_(
					"A phone number is required for the {0} contact. "
					"Please add a phone number to <b>{1}</b> or the <b>{2}</b> address."
				).format(side, contact.full_name or doc.get(contact_field), address.name)
			)

		email = contact.get(email_field)
		if not email:
			frappe.throw(
				_(
					"An email address is required for the {0} contact. " "Please add an email to <b>{1}</b>."
				).format(side, contact.full_name or doc.get(contact_field))
			)

		return {
			"address": {
				"company_name": company_name,
				"address_line1": address.address_line1,
				"address_line2": address.address_line2,
				# "address_line3": None,
				"city_locality": address.city,
				"state_province": address.state,
				"postal_code": address.pincode,
				"country_code": (
					frappe.get_value("Country", address.country, "code") or ""
				).upper(),  # ISO 3166-1 alpha-2 country code
				"residential": bool(doc.get(residential_flag_field)),
			},
			"contact": {
				"name": contact.full_name,
				"phone_number": phone,
				"email": email,
			},
		}


def get_ltl_provider(doc=None) -> BaseLTL:
	"""Return the correct BaseLTL subclass instance for the given Shipment doc.

	Resolution uses the ``ltl_providers`` hook defined in hooks.py — a dict mapping
	a base_url domain substring to a dotted import path (plain strings, no imports).
	The Freight Carrier Settings record for the doc's company and preferred carrier
	is looked up to obtain ``base_url``; the first matching entry in ``ltl_providers``
	wins.  Falls back to ``ShipstationLTL`` when no doc is provided or no entry matches.

	Example hooks.py entry::

	    ltl_providers = {
	        "wwex.com": "my_app.wwex_ltl.WwexLTL",
	    }
	"""
	provider_map: dict[str, str] = frappe.get_hooks("ltl_providers") or {}

	if doc is not None:
		co = get_shipment_company_for_ltl(doc)
		supplier = getattr(doc, "preferred_carrier", None)
		fc = get_freight_carrier_settings(co, supplier) if co and supplier else None
		base_url = (fc.base_url if fc else None) or ""
		url = base_url.strip().lower()
		for domain, dotted_path in provider_map.items():
			if domain.lower() in url:
				# frappe.get_hooks returns list values; take the last entry so that
				# downstream apps can override by appending their own entry.
				if isinstance(dotted_path, list):
					dotted_path = dotted_path[-1]
				return frappe.get_attr(dotted_path)()

	return ShipstationLTL()
