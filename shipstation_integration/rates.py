# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

"""
Rate comparison and estimation using ShipStation API v2.

This module provides functionality for comparing shipping rates across
multiple carriers before purchasing labels.
"""

from typing import TYPE_CHECKING, Optional

import frappe
from frappe import _

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

	shipment = {
		"ship_from": _format_address(ship_from),
		"ship_to": _format_address(ship_to),
		"packages": [_format_package(p) for p in packages],
	}

	try:
		rates_response = client.get_rates_from_shipment(shipment)
		return _format_rates_response(rates_response)
	except Exception as e:
		frappe.log_error(title="Error fetching shipping rates", message=str(e))
		frappe.throw(_("Failed to fetch shipping rates: {0}").format(str(e)))


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
		frappe.log_error(title="Error estimating shipping rates", message=str(e))
		frappe.throw(_("Failed to estimate shipping rates: {0}").format(str(e)))


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
		frappe.log_error(title="Error fetching rate", message=str(e))
		frappe.throw(_("Failed to fetch rate: {0}").format(str(e)))


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

	# Get settings
	settings_name = None
	if dn.integration_doctype == "Shipstation Settings" and dn.integration_doc:
		settings_name = dn.integration_doc

	# Build address dicts
	ship_from = {
		"name": dn.company,
		"street1": ship_from_address.address_line1,
		"street2": ship_from_address.address_line2 or "",
		"city": ship_from_address.city,
		"state": ship_from_address.state,
		"postal_code": ship_from_address.pincode,
		"country": frappe.db.get_value("Country", ship_from_address.country, "code") or "US",
	}

	ship_to = {
		"name": dn.customer_name or dn.customer,
		"street1": ship_to_address.address_line1,
		"street2": ship_to_address.address_line2 or "",
		"city": ship_to_address.city,
		"state": ship_to_address.state,
		"postal_code": ship_to_address.pincode,
		"country": frappe.db.get_value("Country", ship_to_address.country, "code") or "US",
	}

	packages = [
		{
			"weight": {"value": total_weight, "unit": "pound"},
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


def _format_address(address: dict) -> dict:
	"""Format address for ShipStation API v2."""
	return {
		"name": address.get("name", ""),
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
		elif hasattr(rates_response, "rate_response"):
			rate_list = rates_response.rate_response.rates or []
		elif isinstance(rates_response, dict):
			rate_list = rates_response.get("rate_response", {}).get("rates", [])
		else:
			rate_list = []
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

