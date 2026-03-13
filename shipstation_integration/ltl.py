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
from frappe.utils import add_days, comma_or, get_link_to_form, now
from frappe.utils.file_manager import save_file

from shipstation_integration.base_ltl import BaseLTL
from shipstation_integration.carriers import get_or_create_transporter
from shipstation_integration.utils import get_error_message, get_shipstation_settings


class ShipstationLTL(BaseLTL):
	def __init__(self):
		"""
		The self.provider value should be the name of the API service provider
		"""
		self.provider = "Shipstation"

		# Maps ERPNext UOMs with accepted payload values. Length covers length/width/height
		self.uom_map = {
			"length": {"Inch": "inches", "Centimeter": "centimeters"},
			"weight": {"Gram": "grams", "Kilogram": "kilograms", "Ounce": "ounces", "Pound": "pounds"},
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
		self, settings_name: str | None = None, create_transporters: bool = False
	) -> list[dict] | None:
		"""
		List all LTL carriers connected to the ShipStation account.

		Args:
		settings_name: Optional Shipstation Settings document name
		create_transporters: If True, create Supplier records with is_transporter=1
		for each carrier that doesn't already exist

		Returns:
		List of carrier dicts with carrier_id, carrier_code, name, supplier, etc.
		"""
		base_url, headers = self.get_base_url_and_headers(settings_name)
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
					formatted = self._format_ltl_carrier(c)

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

		except Exception as e:
			frappe.log_error(title="Error getting LTL carriers", message=str(e))
			frappe.throw(_("Failed to get LTL carriers - {0}").format(str(e)))

	def get_carrier_id_for_supplier(
		self, supplier_name: str, settings_name: str | None = None
	) -> str | None:
		"""
		Look up the ShipEngine carrier_id for a given Supplier (transporter) name.

		Uses the synced LTL carrier data stored in Shipstation Settings to map
		Supplier names to ShipEngine carrier IDs.

		Args:
		supplier_name: The Supplier document name (e.g., "UPS", "USPS")
		settings_name: Optional Shipstation Settings document name

		Returns:
		ShipEngine carrier_id (e.g., "100abcde-...") or None if not found
		"""
		if not supplier_name:
			return None

		settings = get_shipstation_settings(settings_name)
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

		# Get cached carrier data
		if not settings.shipstation_api_ltl_carrier_data:
			frappe.throw(_("No carrier data found. Please sync carriers first."))

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
		self, carrier_id: str | None = None, settings_name: str | None = None
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
		packages = self.list_ltl_carrier_package_types(carrier_id, settings_name)
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
			doc.preferred_carrier, settings_name
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting LTL carrier service levels",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to collect supported carrier service levels.",
			)
			return options

		svc_levels = self.list_ltl_carrier_services(carrier_id, settings_name)
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
			doc.preferred_carrier, settings_name
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting LTL carrier accessorial services",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to collect supported accessorial services.",
			)
			return default

		accessorial_svcs = self.list_ltl_carrier_accessorial_services(carrier_id, settings_name)
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
			doc.preferred_carrier, settings_name
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting whether LTL carrier supports quote or spot quotes",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to check if supports quotes or spot quotes.",
			)
			return default

		feats = self.list_ltl_carrier_features(carrier_id, settings_name)
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
			doc.preferred_carrier, settings_name
		)
		if not carrier_id:
			frappe.log_error(
				title="Error getting whether LTL carrier supports electronically scheduling a pickup",
				message=f"No {self.provider} carrier ID found for the preferred carrier, unable to check if supports scheduling a pickup via the API.",
			)
			return default

		feats = self.list_ltl_carrier_features(carrier_id, settings_name)
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
		sl, amt = frappe.get_value(
			"Shipment Quotation", doc.accepted_quotation, ["service_level", "grand_total"]
		)
		frappe.set_value(dt, dn, "carrier_service", sl)
		frappe.set_value(dt, dn, "shipment_id", pu_response.get("shipment_id"))
		frappe.set_value(dt, dn, "pickup_id", pu_response.get("pickup_id"))
		frappe.set_value(dt, dn, "awb_number", pu_response.get("pro_number"))
		frappe.set_value(dt, dn, "shipment_amount", amt)

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
	def get_base_url_and_headers(self, settings_name: str | None = None) -> tuple:
		settings = get_shipstation_settings(settings_name)
		api_key = settings.get_password("shipstation_api_key")
		if not api_key:
			frappe.throw(_("ShipStation API key not configured in Shipstation Settings"))
		base_url = settings.base_url
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

	def _format_ltl_carrier(self, carrier) -> dict:
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
			doc.preferred_carrier, settings_name
		)
		if not carrier_id:
			frappe.throw(
				f"No {self.provider} carrier ID found for the preferred carrier - try fetching LTL carriers from Shipstation Settings to collect them."
			)

	def get_ltl_carrier(self, carrier_id: str, settings_name: str | None = None) -> dict:
		"""
		Get details for a specific LTL carrier.

		Args:
		carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name

		Returns:
		LTL carrier details dict
		"""
		base_url, headers = self.get_base_url_and_headers(settings_name)

		try:
			with httpx.Client() as client:
				response = client.get(
					f"{base_url}/v-beta/ltl/carriers/{carrier_id}",
					headers=headers,
				)
				data = response.json()
				response.raise_for_status()
				return self._format_ltl_carrier(data)
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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting carrier", message=error_msg)
			frappe.throw(_("Failed to get carrier: {0}").format(error_msg))

	def list_ltl_carrier_accessorial_services(
		self, carrier_id: str, settings_name: str | None = None
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
		base_url, headers = self.get_base_url_and_headers(settings_name)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier options", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier options: {0}").format(error_msg))

	def list_ltl_carrier_features(
		self, carrier_id: str, settings_name: str | None = None
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
		carrier_data = self.get_ltl_carrier(carrier_id=carrier_id, settings_name=settings_name)
		return carrier_data.get("features", [])

	def list_ltl_carrier_package_types(
		self, carrier_id: str, settings_name: str | None = None
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
		base_url, headers = self.get_base_url_and_headers(settings_name)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier package/container types", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier package/container types: {0}").format(error_msg))

	def get_all_ltl_carrier_package_types(self, settings_name: str | None = None) -> dict:
		"""
		Get package types for all configured LTL carriers.

		Args:
		settings_name: Optional Shipstation Settings document name

		Returns:
		Dict mapping carrier_id to list of package types
		"""
		settings = get_shipstation_settings(settings_name)

		# Get carrier IDs from stored LTL carrier data
		carrier_data = []
		if settings.shipstation_api_ltl_carrier_data:
			carrier_data = json.loads(settings.shipstation_api_ltl_carrier_data)

		if not carrier_data:
			# Fetch carriers if not cached
			carriers = self.list_ltl_carriers(settings_name)
			carrier_ids = [c["carrier_id"] for c in carriers]
		else:
			carrier_ids = [c.get("carrier_id") for c in carrier_data if c.get("carrier_id")]

		result = {}
		for carrier_id in carrier_ids:
			try:
				packages = self.list_ltl_carrier_package_types(carrier_id, settings_name)
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
		self, carrier_id: str, settings_name: str | None = None
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
		base_url, headers = self.get_base_url_and_headers(settings_name)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier options", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier options: {0}").format(error_msg))

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
		base_url, headers = self.get_base_url_and_headers(settings_name)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting LTL quote", message=error_msg)
			frappe.throw(_("Failed to get LTL quote: {0}").format(error_msg))

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
		base_url, headers = self.get_base_url_and_headers(settings_name)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting LTL spot quote", message=error_msg)
			frappe.throw(_("Failed to get LTL spot quote: {0}").format(error_msg))

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
		base_url, headers = self.get_base_url_and_headers(settings_name)
		quote_id = doc.quote_or_offer_id
		if not quote_id:
			frappe.throw(
				f"{self.provider} requires a Quote ID to schedule an LTL pickup. Get LTL quotes then accept one of the generated Supplier Quotations."
			)

		delivery_date = doc.get("estimated_delivery_date") or frappe.get_value(
			"Shipment Quotation", doc.accepted_quotation, "estimated_delivery_date"
		)
		if not doc.get("estimated_delivery_date"):
			frappe.set_value(doc.doctype, doc.name, "estimated_delivery_date", delivery_date)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error scheduling LTL pickup with quote ID", message=error_msg)
			frappe.throw(_("Failed to schedule LTL pickup with quote ID: {0}").format(error_msg))

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
		base_url, headers = self.get_base_url_and_headers(settings_name)
		self.validate_carrier_and_id(doc, settings_name)
		carrier_id = doc.carrier_id

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error scheduling LTL pickup", message=error_msg)
			frappe.throw(_("Failed to schedule LTL pickup: {0}").format(error_msg))

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
		base_url, headers = self.get_base_url_and_headers(settings_name)
		quote_id = doc.quote_or_offer_id
		if not quote_id:
			frappe.throw(
				f"No {self.provider} Quote ID found - make sure to get LTL quotes then accept one of the generated Supplier Quotations."
			)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting BOL using quote ID", message=error_msg)
			frappe.throw(_("Failed to get BOL using quote ID: {0}").format(error_msg))

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
		base_url, headers = self.get_base_url_and_headers(settings_name)
		pickup_id = doc.pickup_id
		if not pickup_id:
			frappe.throw(
				f"No {self.provider} Pickup ID found - make sure to first schedule an LTL pickup to generate this ID."
			)

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error getting BOL using pickup ID", message=error_msg)
			frappe.throw(_("Failed to get BOL using pickup ID: {0}").format(error_msg))

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
		base_url, headers = self.get_base_url_and_headers(settings_name)
		carrier_id = doc.carrier_id
		pro_number = doc.awb_number
		if not pro_number:
			frappe.throw("No PRO Number found - make sure to schedule an LTL pickup first.")

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

		except Exception as e:
			error_msg = get_error_message(e)
			frappe.log_error(title="Error listing LTL carrier documents", message=error_msg)
			frappe.throw(_("Failed to list LTL carrier documents: {0}").format(error_msg))

	def get_and_validate_uoms(self, row: ShipmentParcel) -> tuple:
		"""
		Given a Shipment Parcel row from a Shipment doc, validates the UOMs for length, width, and
		density, then returns the appropriate values to use for each in a Shipstation API call.

		Args:
		row: a Shipment Parcel document (a row in a Shipment's Shipment Parcel child table)

		Returns:
		The value to use for the UOM in the API call, or an Error if Shipstation doesn't accept
		the given UOM.
		"""
		errors = []
		err_template = "The {dim} UOM of {user_uom} used in row {row_idx} is not supported by Shipstation. Please use {supported}."
		len_uom = self.uom_map["length"].get(row.length_uom)
		if not len_uom:
			supported_lengths = list(self.uom_map["length"].keys())
			errors.append(
				err_template.format(
					dim="length", user_uom=row.length_uom, row_idx=row.idx, supported=comma_or(supported_lengths)
				)
			)

		weight_uom = self.uom_map["weight"].get(row.weight_uom)
		if not weight_uom:
			supported_weights = list(self.uom_map["weight"].keys())
			errors.append(
				err_template.format(
					dim="weight", user_uom=row.weight_uom, row_idx=row.idx, supported=comma_or(supported_weights)
				)
			)

		density_uom = self.uom_map["density"].get(row.density_uom)
		if not density_uom:
			supported_densities = list(self.uom_map["density"].keys())
			errors.append(
				err_template.format(
					dim="density",
					user_uom=row.density_uom,
					row_idx=row.idx,
					supported=comma_or(supported_densities),
				)
			)

		if errors:
			frappe.throw(msg=". ".join(errors))
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

		# Packages / Handling Units
		packages = []
		haz_name = ship_from["contact"]["name"]
		haz_phone = ship_from["contact"]["phone_number"]
		for row in doc.shipment_parcel:
			len_uom, weight_uom, density_uom = self.get_and_validate_uoms(row)
			packages.append(  # need an object for each package in shipment
				{
					"code": row.package_type_code,  # ShipEngine package type code
					"freight_class": int(row.nmfc_freight_class),  # NMFC freight class (50-500)
					"density": {"value": row.density, "unit": density_uom},
					"nmfc_code": row.nmfc_commodity_code,  # NMFC commodity code / item number
					"description": doc.get("description_of_content", ""),  # description of what's in container
					"dimensions": {
						"width": row.width,
						"height": row.height,
						"length": row.length,
						"unit": len_uom,  # can be "inches" or "centimeters"
					},
					"weight": {
						"value": row.weight,
						"unit": weight_uom,  # can be "grams", "kilograms", "ounces", or "pounds"
					},
					"quantity": row.count,  # number of packages of this type
					"stackable": bool(row.is_stackable),  # Boolean whether can be safely stacked or not
					"hazardous_materials": bool(
						row.hazardous_material
					),  # Boolean whether package contains hazardous materials or not
				}
			)
			if row.hazardous_material:
				haz_name = row.emergency_name or haz_name
				haz_phone = row.emergency_phone_number or haz_phone

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
		b_type = doc.billing_type.replace(" ", "_").lower()
		b_pmt_terms = doc.payment_terms.replace(" ", "_").lower()
		if b_type not in ["consignee", "shipper", "third_party"]:
			frappe.throw("The Billing Type must be either 'Consignee', 'Shipper', or 'Third Party'.")

		if b_pmt_terms not in ["collect", "prepaid", "third_party"]:
			frappe.throw("The Billing Payment Term must be either 'Collect', 'Prepaid', or 'Third Party'.")

		bill_to = {
			"type": b_type,  # may be "consignee", "shipper", or "third_party"
			"payment_terms": b_pmt_terms,  # may be "collect", "prepaid", or "third_party"
			"account": doc.billing_account,
		}
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
			"service_code": doc.get("carrier_service_level", "stnd"),
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
		Collects necessary data from a Shipment document in ERPNext to populate a shipment object.

		Acceptable length units are: "inches" or "centimeters"
		Acceptable weight units are: "grams", "kilograms", "ounces", or "pounds"

		Args:
		doc: a Shipment document in ERPNext from which to retrieve the shipment info

		Returns:
		shipment object dict that may be used in a get quote or get spot quote request
		"""
		if not doc.get("shipment_parcel"):
			frappe.throw(
				msg=_("Shipment Parcel information needed for length, width, height, and weight UOM data."),
				title=_("Missing Shipment Parcel Information"),
			)
		row = doc.shipment_parcel[0]
		len_uom, weight_uom, density_uom = self.get_and_validate_uoms(row)

		# Linear length and width fields required
		return {
			"total_linear_length": {"value": doc.total_length, "unit": len_uom},
			"total_width": {"value": doc.total_width, "unit": len_uom},
			"total_height": {"value": doc.total_height, "unit": len_uom},
			"total_weight": {"value": doc.total_weight, "unit": weight_uom},
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
		party_name_field_map = {
			"Company": "pickup_company" if ship_from else "delivery_company",
			"Customer": "pickup_customer" if ship_from else "delivery_customer",
			"Supplier": "pickup_supplier" if ship_from else "delivery_supplier",
		}
		party_name_field = party_name_field_map[party_type]
		address_name_field = "pickup_address_name" if ship_from else "delivery_address_name"
		residential_flag_field = "residential_pickup" if ship_from else "residential_delivery"

		if ship_from and party_type == "Company":
			contact_field = "pickup_contact_person"
			contact_dt = "User"
			email_field = "email"
		else:
			contact_field = "pickup_contact_name" if ship_from else "delivery_contact_name"
			contact_dt = "Contact"
			email_field = "email_id"

		address = frappe.get_doc("Address", doc.get(address_name_field))
		contact = frappe.get_doc(contact_dt, doc.get(contact_field))

		return {
			"address": {
				"company_name": doc.get(party_name_field),
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
				"phone_number": contact.phone,
				"email": contact.get(email_field),
			},
		}
