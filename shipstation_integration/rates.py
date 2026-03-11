# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

"""
Rate comparison and estimation using ShipStation API v2.

This module provides functionality for comparing shipping rates across
multiple carriers before purchasing labels.
"""

import json
from typing import TYPE_CHECKING, Optional

import frappe
from frappe import _
from frappe.utils import flt
from shipengine.errors import ShipEngineError

from shipstation_integration.utils import get_error_message, get_shipstation_settings

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


# UOM mappings for ShipEngine API
# ShipEngine accepts: pound, ounce, gram, kilogram for weight
# ShipEngine accepts: inch, centimeter for dimensions
DIMENSION_UOM_MAP = {
	"Inch": "inch",
	"Centimeter": "centimeter",
	"inch": "inch",
	"centimeter": "centimeter",
	"cm": "centimeter",
	"in": "inch",
}

WEIGHT_UOM_MAP = {
	"Pound": "pound",
	"Kg": "kilogram",
	"Kilogram": "kilogram",  # alias — not an ERPNext UOM but kept for robustness
	"Ounce": "ounce",
	"Gram": "gram",
	"pound": "pound",
	"kilogram": "kilogram",
	"ounce": "ounce",
	"gram": "gram",
	"lb": "pound",
	"Lb": "pound",
	"kg": "kilogram",
	"oz": "ounce",
	"g": "gram",
}


def get_state_code(state: str, country_code: str = "US") -> str:
	"""
	Convert state name to 2-character state code for US addresses.

	ShipEngine requires 2-character state codes for US addresses.
	For non-US addresses, returns the state as-is.

	Args:
	        state: State name or code (e.g., "California" or "CA")
	        country_code: 2-character country code

	Returns:
	        2-character state code for US, original value for other countries
	"""
	if not state:
		return ""

	# If already 2 characters, assume it's a code
	if len(state) <= 2:
		return state.upper()

	# Only convert for US addresses
	if country_code.upper() != "US":
		return state

	# US state name to code mapping
	us_states = {
		"Alabama": "AL",
		"Alaska": "AK",
		"Arizona": "AZ",
		"Arkansas": "AR",
		"California": "CA",
		"Colorado": "CO",
		"Connecticut": "CT",
		"Delaware": "DE",
		"Florida": "FL",
		"Georgia": "GA",
		"Hawaii": "HI",
		"Idaho": "ID",
		"Illinois": "IL",
		"Indiana": "IN",
		"Iowa": "IA",
		"Kansas": "KS",
		"Kentucky": "KY",
		"Louisiana": "LA",
		"Maine": "ME",
		"Maryland": "MD",
		"Massachusetts": "MA",
		"Michigan": "MI",
		"Minnesota": "MN",
		"Mississippi": "MS",
		"Missouri": "MO",
		"Montana": "MT",
		"Nebraska": "NE",
		"Nevada": "NV",
		"New Hampshire": "NH",
		"New Jersey": "NJ",
		"New Mexico": "NM",
		"New York": "NY",
		"North Carolina": "NC",
		"North Dakota": "ND",
		"Ohio": "OH",
		"Oklahoma": "OK",
		"Oregon": "OR",
		"Pennsylvania": "PA",
		"Rhode Island": "RI",
		"South Carolina": "SC",
		"South Dakota": "SD",
		"Tennessee": "TN",
		"Texas": "TX",
		"Utah": "UT",
		"Vermont": "VT",
		"Virginia": "VA",
		"Washington": "WA",
		"West Virginia": "WV",
		"Wisconsin": "WI",
		"Wyoming": "WY",
		"District of Columbia": "DC",
		# Territories
		"Puerto Rico": "PR",
		"Guam": "GU",
		"American Samoa": "AS",
		"U.S. Virgin Islands": "VI",
		"Northern Mariana Islands": "MP",
	}

	# Case-insensitive lookup
	for name, code in us_states.items():
		if name.lower() == state.lower():
			return code

	# If no match found, return first 2 characters uppercase as last resort
	return state[:2].upper()


@frappe.whitelist()
def get_rates(
	ship_from: dict,
	ship_to: dict,
	packages: list[dict],
	settings_name: str | None = None,
) -> list[dict]:
	"""
	Get rate quotes from multiple carriers.

	Args:
	        ship_from: Origin address dict with keys: name, street1, city, state, postal_code, country
	        ship_to: Destination address dict with same keys
	        packages: List of package dicts with keys: weight (dict with value/unit), dimensions (optional)
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        List of rate quotes from available carriers
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	# Build the shipment object
	shipment_data = {
		"ship_from": format_address(ship_from),
		"ship_to": format_address(ship_to),
		"packages": [format_package(p) for p in packages],
	}

	# ShipEngine API expects a rate_options + shipment structure
	rate_request = {
		"shipment": shipment_data,
		"rate_options": {
			"carrier_ids": get_carrier_ids(settings),
		},
	}

	try:
		rates_response = client.get_rates_from_shipment(rate_request)
		result = format_rates_response(rates_response)
		if not result:
			# Log the full response for debugging if no rates found
			frappe.logger("shipstation").info(
				f"No rates found. Request: {rate_request}, Response: {rates_response}"
			)
		return result
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(
			title="Error fetching shipping rates",
			message=f"Rate Request: {rate_request}\n\nError: {error_msg}",
		)
		frappe.throw(_("Failed to fetch shipping rates: {0}").format(error_msg))


@frappe.whitelist()
def estimate_rates(
	from_postal_code: str,
	from_country: str,
	to_postal_code: str,
	to_country: str,
	weight_value: float,
	weight_unit: str = "pound",
	settings_name: str | None = None,
) -> list[dict]:
	"""
	Quick rate estimation with minimal address information.

	Args:
	        from_postal_code: Origin postal/zip code
	        from_country: Origin country code (e.g., "US")
	        to_postal_code: Destination postal/zip code
	        to_country: Destination country code
	        weight_value: Package weight
	        weight_unit: Weight unit (pound, ounce, gram, kilogram)
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        List of estimated rates
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	estimate_request = {
		"from_postal_code": from_postal_code,
		"from_country_code": from_country.upper(),
		"to_postal_code": to_postal_code,
		"to_country_code": to_country.upper(),
		"weight": {
			"value": weight_value,
			"unit": weight_unit,
		},
	}

	try:
		rates = client.estimate_rates(estimate_request)
		return format_rates_response(rates)
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(title="Error estimating shipping rates", message=error_msg)
		frappe.throw(_("Failed to estimate shipping rates: {0}").format(error_msg))


@frappe.whitelist()
def get_rate_by_id(rate_id: str, settings_name: str | None = None) -> dict:
	"""
	Retrieve a previously cached rate by its ID.

	Args:
	        rate_id: The rate ID returned from a previous rate request
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Rate details dict
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		rate = client.get_rate_by_id(rate_id)
		return format_single_rate(rate)
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(title="Error fetching rate", message=error_msg)
		frappe.throw(_("Failed to fetch rate: {0}").format(error_msg))


@frappe.whitelist()
def get_rates_for_packing_slip(packing_slip: str) -> list[dict]:
	"""
	Get shipping rates for a Packing Slip.

	Each Packing Slip represents one physical package. Addresses are read
	from the Packing Slip's shipping_address_name and dispatch_address_name fields.
	Package dimensions come from the Parcel Dimensions child table.

	Args:
	        packing_slip: Packing Slip document name

	Returns:
	        List of available shipping rates
	"""
	ps = frappe.get_doc("Packing Slip", packing_slip)

	if not ps.shipping_address_name:
		frappe.throw(_("Packing Slip must have a shipping address"))

	if not ps.dispatch_address_name:
		frappe.throw(_("Packing Slip must have a dispatch (ship from) address"))

	# Get addresses
	ship_to_address = frappe.get_doc("Address", ps.shipping_address_name)
	ship_from_address = frappe.get_doc("Address", ps.dispatch_address_name)

	# Get company name from linked Delivery Note
	dn = frappe.get_doc("Delivery Note", ps.delivery_note)
	company_name = dn.company
	customer_name = dn.customer_name or dn.customer

	# Build address dicts - include phone which is required by ShipEngine
	# State must be 2-character code for US addresses
	ship_from_country = frappe.db.get_value("Country", ship_from_address.country, "code") or "US"
	ship_to_country = frappe.db.get_value("Country", ship_to_address.country, "code") or "US"

	ship_from = {
		"name": company_name,
		"street1": ship_from_address.address_line1,
		"street2": ship_from_address.address_line2 or "",
		"city": ship_from_address.city,
		"state": get_state_code(ship_from_address.state, ship_from_country),
		"postal_code": ship_from_address.pincode,
		"country": ship_from_country,
		"phone": ship_from_address.phone or "0000000000",
	}

	ship_to = {
		"name": customer_name,
		"street1": ship_to_address.address_line1,
		"street2": ship_to_address.address_line2 or "",
		"city": ship_to_address.city,
		"state": get_state_code(ship_to_address.state, ship_to_country),
		"postal_code": ship_to_address.pincode,
		"country": ship_to_country,
		"phone": ship_to_address.phone or "0000000000",
	}

	# Build package from this Packing Slip's item parcel dimensions
	package = get_package_from_packing_slip(ps)
	if not package:
		frappe.throw(_("Packing Slip must have items with a Parcel # and parcel dimensions configured"))

	return get_rates(
		ship_from=ship_from,
		ship_to=ship_to,
		packages=[package],
	)


def get_package_from_packing_slip(packing_slip, parcel_number: int | None = None) -> dict | None:
	"""
	Build a package dict from a Packing Slip document.

	Reads parcel dimensions from the first Packing Slip Item row matching
	``parcel_number``. If ``parcel_number`` is not provided, uses the first
	item with any parcel_number assigned. Falls back to the Packing Slip gross
	weight fields if no item has parcel dimensions.

	Args:
	        packing_slip: Packing Slip document
	        parcel_number: Specific parcel number to build the package for.
	                When None, the first packed item is used (rate-request behaviour).

	Returns:
	        Package dict for rate request, or None if no valid data
	"""
	parcel_item = None
	for item in getattr(packing_slip, "items", []):
		if item.parcel_number and (parcel_number is None or item.parcel_number == parcel_number):
			parcel_item = item
			break

	if parcel_item:
		dimension_unit = DIMENSION_UOM_MAP.get(parcel_item.dimension_uom, "inch")
		weight_unit = WEIGHT_UOM_MAP.get(parcel_item.parcel_weight_uom, "pound")

		return {
			"weight": {
				"value": flt(parcel_item.parcel_weight) or 1.0,
				"unit": weight_unit,
			},
			"dimensions": {
				"length": flt(parcel_item.parcel_length) or 1,
				"width": flt(parcel_item.parcel_width) or 1,
				"height": flt(parcel_item.parcel_height) or 1,
				"unit": dimension_unit,
			},
		}

	# Fallback: use Packing Slip gross weight if available
	if packing_slip.gross_weight_pkg:
		weight_unit = WEIGHT_UOM_MAP.get(packing_slip.gross_weight_uom, "pound")
		return {
			"weight": {
				"value": flt(packing_slip.gross_weight_pkg),
				"unit": weight_unit,
			},
			"dimensions": {
				"length": 12,
				"width": 9,
				"height": 6,
				"unit": "inch",
			},
		}

	return None


@frappe.whitelist()
def get_rates_for_delivery_note(delivery_note: str) -> list[dict]:
	"""
	Get shipping rates for a Delivery Note.

	Addresses are read from the Delivery Note's shipping_address_name and
	dispatch_address_name fields. Package weight is calculated from item weights.

	Args:
	        delivery_note: Delivery Note document name

	Returns:
	        List of available shipping rates
	"""
	dn = frappe.get_doc("Delivery Note", delivery_note)

	if not dn.shipping_address_name:
		frappe.throw(_("Delivery Note must have a shipping address"))

	if not dn.dispatch_address_name:
		frappe.throw(_("Delivery Note must have a dispatch (ship from) address"))

	ship_to_address = frappe.get_doc("Address", dn.shipping_address_name)
	ship_from_address = frappe.get_doc("Address", dn.dispatch_address_name)

	ship_from_country = frappe.db.get_value("Country", ship_from_address.country, "code") or "US"
	ship_to_country = frappe.db.get_value("Country", ship_to_address.country, "code") or "US"

	ship_from = {
		"name": dn.company,
		"street1": ship_from_address.address_line1,
		"street2": ship_from_address.address_line2 or "",
		"city": ship_from_address.city,
		"state": get_state_code(ship_from_address.state, ship_from_country),
		"postal_code": ship_from_address.pincode,
		"country": ship_from_country,
		"phone": ship_from_address.phone or "0000000000",
	}

	ship_to = {
		"name": dn.customer_name or dn.customer,
		"street1": ship_to_address.address_line1,
		"street2": ship_to_address.address_line2 or "",
		"city": ship_to_address.city,
		"state": get_state_code(ship_to_address.state, ship_to_country),
		"postal_code": ship_to_address.pincode,
		"country": ship_to_country,
		"phone": ship_to_address.phone or "0000000000",
	}

	package = get_fallback_package(dn)

	return get_rates(
		ship_from=ship_from,
		ship_to=ship_to,
		packages=[package],
	)


def get_fallback_package(dn) -> dict:
	"""
	Create a fallback package using calculated weight from Delivery Note items.

	Args:
	        dn: Delivery Note document

	Returns:
	        Package dict with default dimensions
	"""
	# Calculate total weight from items
	total_weight = 0.0
	for item in dn.items:
		item_weight = frappe.db.get_value("Item", item.item_code, "weight_per_unit") or 0
		total_weight += item_weight * item.qty

	if total_weight <= 0:
		total_weight = 1.0  # Default to 1 lb if no weight

	return {
		"weight": {"value": total_weight, "unit": "pound"},
		"dimensions": {
			"length": 12,  # Default dimensions in inches
			"width": 9,
			"height": 6,
			"unit": "inch",
		},
	}


def get_carrier_ids(settings: "ShipstationSettings") -> list[str]:
	"""Get list of carrier IDs from settings."""
	carrier_ids = []
	if settings.shipstation_api_carrier_data:
		carriers = json.loads(settings.shipstation_api_carrier_data)
		carrier_ids = [c.get("carrier_id") for c in carriers if c.get("carrier_id")]
	return carrier_ids


def format_address(address: dict) -> dict:
	"""Format address for ShipStation API v2."""
	return {
		"name": address.get("name", ""),
		"phone": address.get("phone", "0000000000"),
		"address_line1": address.get("street1", address.get("address_line1", "")),
		"address_line2": address.get("street2", address.get("address_line2", "")),
		"city_locality": address.get("city", address.get("city_locality", "")),
		"state_province": address.get("state", address.get("state_province", "")),
		"postal_code": address.get("postal_code", address.get("pincode", "")),
		"country_code": address.get("country", address.get("country_code", "US")).upper()[:2],
	}


def format_package(package: dict) -> dict:
	"""Format package for ShipStation API v2."""
	formatted = {
		"weight": {
			"value": package.get("weight", {}).get("value", 1),
			"unit": package.get("weight", {}).get("unit", "pound"),
		}
	}

	if package.get("dimensions"):
		formatted["dimensions"] = {
			"length": package["dimensions"].get("length", 1),
			"width": package["dimensions"].get("width", 1),
			"height": package["dimensions"].get("height", 1),
			"unit": package["dimensions"].get("unit", "inch"),
		}

	return formatted


def format_rates_response(rates_response) -> list[dict]:
	"""Format rates response for frontend consumption."""
	rates = []

	try:
		if isinstance(rates_response, list):
			rate_list = rates_response
		elif isinstance(rates_response, dict):
			# ShipEngine returns response with rate_response.rates structure
			rate_response = rates_response.get("rate_response", rates_response)
			if isinstance(rate_response, dict):
				rate_list = rate_response.get("rates", [])
			else:
				rate_list = []
		elif hasattr(rates_response, "rate_response"):
			rate_list = rates_response.rate_response.rates or []
		else:
			rate_list = []

		# Log for debugging
		if not rate_list:
			frappe.logger("shipstation").info(
				f"Rates response structure: {type(rates_response)}, keys: {rates_response.keys() if isinstance(rates_response, dict) else 'N/A'}"
			)
	except Exception as e:
		frappe.logger("shipstation").warning(f"Failed to parse rates response: {e}")
		rate_list = []

	for rate in rate_list:
		rates.append(format_single_rate(rate))

	# Sort by shipping amount
	rates.sort(key=lambda x: x.get("shipping_amount", {}).get("amount", 999999))

	return rates


def format_single_rate(rate) -> dict:
	"""Format a single rate for frontend consumption."""
	if isinstance(rate, dict):
		return {
			"rate_id": rate.get("rate_id"),
			"carrier_id": rate.get("carrier_id"),
			"carrier_code": rate.get("carrier_code"),
			"carrier_name": rate.get("carrier_friendly_name", rate.get("carrier_nickname", "")),
			"service_code": rate.get("service_code"),
			"service_type": rate.get("service_type"),
			"shipping_amount": rate.get("shipping_amount", {}),
			"insurance_amount": rate.get("insurance_amount", {}),
			"confirmation_amount": rate.get("confirmation_amount", {}),
			"other_amount": rate.get("other_amount", {}),
			"delivery_days": rate.get("delivery_days"),
			"estimated_delivery_date": rate.get("estimated_delivery_date"),
			"carrier_delivery_days": rate.get("carrier_delivery_days"),
			"ship_date": rate.get("ship_date"),
			"package_type": rate.get("package_type"),
			"trackable": rate.get("trackable", True),
		}
	return rate
