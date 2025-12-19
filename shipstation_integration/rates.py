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

try:
	from shipengine.errors import ShipEngineError
except ImportError:
	ShipEngineError = None

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


@frappe.whitelist()
def get_rates(
	ship_from: dict,
	ship_to: dict,
	packages: list[dict],
	settings_name: Optional[str] = None,
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
	settings = _get_settings(settings_name)
	client = settings.shipstation_api_client()

	# Build the shipment object
	shipment_data = {
		"ship_from": _format_address(ship_from),
		"ship_to": _format_address(ship_to),
		"packages": [_format_package(p) for p in packages],
	}

	# ShipEngine API expects a rate_options + shipment structure
	rate_request = {
		"shipment": shipment_data,
		"rate_options": {
			"carrier_ids": _get_carrier_ids(settings),
		},
	}

	try:
		rates_response = client.get_rates_from_shipment(rate_request)
		result = _format_rates_response(rates_response)
		if not result:
			# Log the full response for debugging if no rates found
			frappe.logger("shipstation").info(f"No rates found. Request: {rate_request}, Response: {rates_response}")
		return result
	except Exception as e:
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error fetching shipping rates", message=f"Rate Request: {rate_request}\n\nError: {error_msg}")
		frappe.throw(_("Failed to fetch shipping rates: {0}").format(error_msg))


@frappe.whitelist()
def estimate_rates(
	from_postal_code: str,
	from_country: str,
	to_postal_code: str,
	to_country: str,
	weight_value: float,
	weight_unit: str = "pound",
	settings_name: Optional[str] = None,
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
	settings = _get_settings(settings_name)
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
		return _format_rates_response(rates)
	except Exception as e:
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error estimating shipping rates", message=error_msg)
		frappe.throw(_("Failed to estimate shipping rates: {0}").format(error_msg))


@frappe.whitelist()
def get_rate_by_id(rate_id: str, settings_name: Optional[str] = None) -> dict:
	"""
	Retrieve a previously cached rate by its ID.

	Args:
		rate_id: The rate ID returned from a previous rate request
		settings_name: Optional Shipstation Settings document name

	Returns:
		Rate details dict
	"""
	settings = _get_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		rate = client.get_rate_by_id(rate_id)
		return _format_single_rate(rate)
	except Exception as e:
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error fetching rate", message=error_msg)
		frappe.throw(_("Failed to fetch rate: {0}").format(error_msg))


@frappe.whitelist()
def get_rates_for_delivery_note(delivery_note: str) -> list[dict]:
	"""
	Get shipping rates for a Delivery Note document.

	Args:
		delivery_note: Delivery Note document name

	Returns:
		List of available shipping rates
	"""
	dn = frappe.get_doc("Delivery Note", delivery_note)

	if not dn.shipping_address_name:
		frappe.throw(_("Delivery Note must have a shipping address"))

	# Get shipping address
	ship_to_address = frappe.get_doc("Address", dn.shipping_address_name)

	# Get company address (ship from)
	company_address = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Company", "link_name": dn.company, "parenttype": "Address"},
		"parent",
	)

	if not company_address:
		frappe.throw(_("Company must have a primary address configured"))

	ship_from_address = frappe.get_doc("Address", company_address)

	# Calculate total weight from items
	total_weight = 0.0
	for item in dn.items:
		item_weight = frappe.db.get_value("Item", item.item_code, "weight_per_unit") or 0
		total_weight += item_weight * item.qty

	if total_weight <= 0:
		total_weight = 1.0  # Default to 1 lb if no weight

	# Get settings - use getattr for fields that may not exist yet
	settings_name = None
	if getattr(dn, "integration_doctype", None) == "Shipstation Settings" and getattr(dn, "integration_doc", None):
		settings_name = dn.integration_doc

	# Build address dicts - include phone which is required by ShipEngine
	ship_from = {
		"name": dn.company,
		"street1": ship_from_address.address_line1,
		"street2": ship_from_address.address_line2 or "",
		"city": ship_from_address.city,
		"state": ship_from_address.state,
		"postal_code": ship_from_address.pincode,
		"country": frappe.db.get_value("Country", ship_from_address.country, "code") or "US",
		"phone": ship_from_address.phone or "0000000000",
	}

	ship_to = {
		"name": dn.customer_name or dn.customer,
		"street1": ship_to_address.address_line1,
		"street2": ship_to_address.address_line2 or "",
		"city": ship_to_address.city,
		"state": ship_to_address.state,
		"postal_code": ship_to_address.pincode,
		"country": frappe.db.get_value("Country", ship_to_address.country, "code") or "US",
		"phone": ship_to_address.phone or "0000000000",
	}

	# Package with weight and dimensions (dimensions required by some carriers like FedEx)
	# TODO: In the future, pull actual dimensions from Shipment Parcel if available
	packages = [
		{
			"weight": {"value": total_weight, "unit": "pound"},
			"dimensions": {
				"length": 12,  # Default dimensions in inches
				"width": 9,
				"height": 6,
				"unit": "inch",
			},
		}
	]

	return get_rates(
		ship_from=ship_from,
		ship_to=ship_to,
		packages=packages,
		settings_name=settings_name,
	)


def _get_settings(settings_name: Optional[str] = None) -> "ShipstationSettings":
	"""Get Shipstation Settings document."""
	if settings_name:
		return frappe.get_doc("Shipstation Settings", settings_name)

	# Find first enabled settings with API v2 enabled
	settings_list = frappe.get_all(
		"Shipstation Settings",
		filters={"enabled": 1, "enable_shipstation_api": 1},
		limit=1,
	)

	if not settings_list:
		frappe.throw(_("No Shipstation Settings found with ShipStation API v2 enabled"))

	return frappe.get_doc("Shipstation Settings", settings_list[0].name)


def _get_error_message(e: Exception) -> str:
	"""Extract error message from ShipEngineError or other exceptions."""
	# ShipEngineError has a message attribute but str(e) returns empty
	if ShipEngineError and isinstance(e, ShipEngineError):
		if hasattr(e, "message") and e.message:
			error_details = [e.message]
			if hasattr(e, "error_code") and e.error_code:
				error_details.append(f"Code: {e.error_code}")
			if hasattr(e, "error_type") and e.error_type:
				error_details.append(f"Type: {e.error_type}")
			return " | ".join(error_details)
		# Try to_dict() as fallback
		if hasattr(e, "to_dict"):
			error_dict = e.to_dict()
			return error_dict.get("message", str(error_dict))
	# For other exceptions, just use str()
	return str(e) or repr(e)


def _get_carrier_ids(settings: "ShipstationSettings") -> list[str]:
	"""Get list of carrier IDs from settings."""
	carrier_ids = []
	if settings.shipstation_api_carrier_data:
		carriers = json.loads(settings.shipstation_api_carrier_data)
		carrier_ids = [c.get("carrier_id") for c in carriers if c.get("carrier_id")]
	return carrier_ids


def _format_address(address: dict) -> dict:
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


def _format_package(package: dict) -> dict:
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


def _format_rates_response(rates_response) -> list[dict]:
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
			frappe.logger("shipstation").info(f"Rates response structure: {type(rates_response)}, keys: {rates_response.keys() if isinstance(rates_response, dict) else 'N/A'}")
	except Exception as e:
		frappe.logger("shipstation").warning(f"Failed to parse rates response: {e}")
		rate_list = []

	for rate in rate_list:
		rates.append(_format_single_rate(rate))

	# Sort by shipping amount
	rates.sort(key=lambda x: x.get("shipping_amount", {}).get("amount", 999999))

	return rates


def _format_single_rate(rate) -> dict:
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

