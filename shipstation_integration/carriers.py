# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

"""
Carrier management for ShipStation API v2.

This module provides functionality for managing carriers and their package types
using the ShipEngine API (ShipStation API v2).
"""

import json
import re
from typing import TYPE_CHECKING, Optional

import frappe
import httpx
from frappe import _
from shipengine.errors import ShipEngineError

from shipstation_integration.utils import get_error_message, get_shipstation_settings

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


# ShipEngine API base URL
SHIPENGINE_API_URL = "https://api.shipengine.com/v1"


def get_or_create_transporter(carrier_name: str) -> str | None:
	"""
	Get or create a Supplier record for a carrier/transporter.

	Matches by name + is_transporter first to allow reasonable deduplication.
	Creates a new Supplier with is_transporter=1 if not found.

	Args:
	        carrier_name: The carrier name (e.g., "UPS", "USPS", "FedEx")

	Returns:
	        Supplier name if found/created, None if carrier_name is empty
	"""
	if not carrier_name:
		return None

	# Normalize the carrier name for matching
	normalized_name = carrier_name.strip()
	if not normalized_name:
		return None

	# First, try to find an existing Supplier with this name and is_transporter=1
	existing = frappe.db.get_value(
		"Supplier",
		{"supplier_name": normalized_name, "is_transporter": 1},
		"name",
	)
	if existing:
		return existing

	# Try a case-insensitive search
	existing = frappe.get_all(
		"Supplier",
		filters={"LOWER(supplier_name)": "LOWER(__PH0__)", "is_transporter": "1"},
		fields=["name"],
		limit=1,
	)
	if existing:
		return existing[0].name

	# No existing transporter found - create a new one
	try:
		# Get default supplier group
		default_group = frappe.db.get_single_value("Buying Settings", "supplier_group")
		if not default_group:
			default_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")

		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": normalized_name,
				"supplier_group": default_group,
				"is_transporter": 1,
			}
		)
		supplier.insert(ignore_permissions=True)
		return supplier.name

	except frappe.DuplicateEntryError:
		# Race condition - another process created it, fetch and return
		existing = frappe.db.get_value(
			"Supplier",
			{"supplier_name": normalized_name},
			"name",
		)
		if existing:
			# Update is_transporter if needed
			frappe.db.set_value("Supplier", existing, "is_transporter", 1)
			return existing
		return None
	except Exception as e:
		frappe.log_error(
			title=f"Error creating transporter: {normalized_name}",
			message=str(e),
		)
		return None


# Known package dimensions (in inches) for common carrier packages
# These are standard sizes that carriers don't always provide via API
KNOWN_PACKAGE_DIMENSIONS: dict[str, dict] = {
	# USPS Flat Rate Boxes and Envelopes
	"flat_rate_envelope": {"length": 12.5, "width": 9.5, "height": 0.75, "unit": "inch"},
	"flat_rate_legal_envelope": {"length": 15.0, "width": 9.5, "height": 0.75, "unit": "inch"},
	"flat_rate_padded_envelope": {"length": 12.5, "width": 9.5, "height": 1.0, "unit": "inch"},
	"small_flat_rate_box": {"length": 8.69, "width": 5.44, "height": 1.75, "unit": "inch"},
	"medium_flat_rate_box": {"length": 11.25, "width": 8.75, "height": 6.0, "unit": "inch"},
	"large_flat_rate_box": {"length": 12.25, "width": 12.25, "height": 6.0, "unit": "inch"},
	"regional_rate_box_a": {"length": 10.125, "width": 7.125, "height": 5.0, "unit": "inch"},
	"regional_rate_box_b": {"length": 12.25, "width": 10.5, "height": 5.5, "unit": "inch"},
	"letter": {"length": 11.5, "width": 6.125, "height": 0.25, "unit": "inch"},
	"large_envelope_or_flat": {"length": 15.0, "width": 12.0, "height": 0.75, "unit": "inch"},
	"thick_envelope": {"length": 11.5, "width": 6.125, "height": 1.0, "unit": "inch"},
	# UPS Boxes and Envelopes
	"ups_letter": {"length": 12.5, "width": 9.5, "height": 0.25, "unit": "inch"},
	"ups_express_pak": {"length": 16.0, "width": 12.75, "height": 2.0, "unit": "inch"},
	"ups_express_box_small": {"length": 13.0, "width": 11.0, "height": 2.0, "unit": "inch"},
	"ups_express_box": {
		"length": 13.0,
		"width": 11.0,
		"height": 2.0,
		"unit": "inch",
	},  # Same as small
	"ups_express_box_medium": {"length": 16.0, "width": 11.0, "height": 3.0, "unit": "inch"},
	"ups__express_box_large": {
		"length": 18.0,
		"width": 13.0,
		"height": 3.0,
		"unit": "inch",
	},  # Note: double underscore in API
	"ups_10_kg_box": {"length": 16.5, "width": 13.25, "height": 10.75, "unit": "inch"},
	"ups_25_kg_box": {"length": 19.75, "width": 17.75, "height": 13.25, "unit": "inch"},
	"ups_tube": {"length": 38.0, "width": 6.0, "height": 6.0, "unit": "inch"},
	# GlobalPost (uses USPS infrastructure, similar sizes)
	"globalpost_flat_rate_envelope": {"length": 12.5, "width": 9.5, "height": 0.75, "unit": "inch"},
	"globalpost_legal_flat_rate_envelope": {
		"length": 15.0,
		"width": 9.5,
		"height": 0.75,
		"unit": "inch",
	},
	"globalpost_padded_flat_rate_envelope": {
		"length": 12.5,
		"width": 9.5,
		"height": 1.0,
		"unit": "inch",
	},
	"globalpost_small_flat_rate_box": {"length": 8.69, "width": 5.44, "height": 1.75, "unit": "inch"},
	"globalpost_flat": {"length": 15.0, "width": 12.0, "height": 0.75, "unit": "inch"},
	"globalpost_tube": {"length": 38.0, "width": 6.0, "height": 6.0, "unit": "inch"},
	# FedEx (in case they're added later)
	"fedex_envelope": {"length": 12.5, "width": 9.5, "height": 0.75, "unit": "inch"},
	"fedex_pak": {"length": 15.5, "width": 12.0, "height": 2.0, "unit": "inch"},
	"fedex_small_box": {"length": 12.25, "width": 10.9, "height": 1.5, "unit": "inch"},
	"fedex_medium_box": {"length": 13.25, "width": 11.5, "height": 2.38, "unit": "inch"},
	"fedex_large_box": {"length": 17.88, "width": 12.38, "height": 3.0, "unit": "inch"},
	"fedex_extra_large_box": {"length": 15.75, "width": 14.13, "height": 6.0, "unit": "inch"},
	"fedex_tube": {"length": 38.0, "width": 6.0, "height": 6.0, "unit": "inch"},
	"fedex_10_kg_box": {"length": 15.81, "width": 12.94, "height": 10.19, "unit": "inch"},
	"fedex_25_kg_box": {"length": 21.56, "width": 16.56, "height": 13.19, "unit": "inch"},
}


@frappe.whitelist()
def list_carriers(
	settings_name: str | None = None, create_transporters: bool = False
) -> list[dict]:
	"""
	List all carriers connected to the ShipStation account.

	Args:
	        settings_name: Optional Shipstation Settings document name
	        create_transporters: If True, create Supplier records with is_transporter=1
	                for each carrier that doesn't already exist

	Returns:
	        List of carrier dicts with carrier_id, carrier_code, name, supplier, etc.
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		response = client.list_carriers()
		carriers = response.get("carriers", []) if isinstance(response, dict) else response

		result = []
		for c in carriers:
			formatted = _format_carrier(c)

			# Optionally create transporter Supplier
			if create_transporters and formatted.get("name"):
				supplier_name = get_or_create_transporter(formatted["name"])
				formatted["supplier"] = supplier_name

			result.append(formatted)

		return result
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(title="Error listing carriers", message=error_msg)
		frappe.throw(_("Failed to list carriers: {0}").format(error_msg))


@frappe.whitelist()
def get_carrier(carrier_id: str, settings_name: str | None = None) -> dict:
	"""
	Get details for a specific carrier.

	Args:
	        carrier_id: The ShipEngine carrier ID (e.g., "se-123456")
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Carrier details dict
	"""
	settings = get_shipstation_settings(settings_name)
	api_key = settings.get_password("shipstation_api_key")

	try:
		with httpx.Client() as client:
			response = client.get(
				f"{SHIPENGINE_API_URL}/carriers/{carrier_id}",
				headers={"API-Key": api_key},
			)
			response.raise_for_status()
			return _format_carrier(response.json())
	except httpx.HTTPStatusError as e:
		frappe.log_error(
			title="Error getting carrier",
			message=f"Carrier ID: {carrier_id}\nStatus: {e.response.status_code}\nResponse: {e.response.text}",
		)
		frappe.throw(_("Failed to get carrier: {0}").format(e.response.text))
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(title="Error getting carrier", message=error_msg)
		frappe.throw(_("Failed to get carrier: {0}").format(error_msg))


@frappe.whitelist()
def list_carrier_package_types(carrier_id: str, settings_name: str | None = None) -> list[dict]:
	"""
	List all package types for a specific carrier.

	This returns carrier-specific package types (e.g., FedEx Pak, USPS Flat Rate Box)
	that can be used when creating shipments and labels.

	Args:
	        carrier_id: The ShipEngine carrier ID (e.g., "se-123456")
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        List of package type dicts with package_id, package_code, name, dimensions, description
	"""
	settings = get_shipstation_settings(settings_name)
	api_key = settings.get_password("shipstation_api_key")

	try:
		with httpx.Client() as client:
			response = client.get(
				f"{SHIPENGINE_API_URL}/carriers/{carrier_id}/packages",
				headers={"API-Key": api_key},
			)
			response.raise_for_status()
			data = response.json()
			packages = data.get("packages", [])

			return [_format_package_type(p) for p in packages]
	except httpx.HTTPStatusError as e:
		frappe.log_error(
			title="Error listing carrier package types",
			message=f"Carrier ID: {carrier_id}\nStatus: {e.response.status_code}\nResponse: {e.response.text}",
		)
		frappe.throw(_("Failed to list carrier package types: {0}").format(e.response.text))
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(title="Error listing carrier package types", message=error_msg)
		frappe.throw(_("Failed to list carrier package types: {0}").format(error_msg))


@frappe.whitelist()
def list_carrier_services(carrier_id: str, settings_name: str | None = None) -> list[dict]:
	"""
	List all services for a specific carrier.

	Args:
	        carrier_id: The ShipEngine carrier ID (e.g., "se-123456")
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        List of service dicts with service_code, name, domestic, international flags
	"""
	settings = get_shipstation_settings(settings_name)
	api_key = settings.get_password("shipstation_api_key")

	try:
		with httpx.Client() as client:
			response = client.get(
				f"{SHIPENGINE_API_URL}/carriers/{carrier_id}/services",
				headers={"API-Key": api_key},
			)
			response.raise_for_status()
			data = response.json()
			services = data.get("services", [])

			return [_format_service(s) for s in services]
	except httpx.HTTPStatusError as e:
		frappe.log_error(
			title="Error listing carrier services",
			message=f"Carrier ID: {carrier_id}\nStatus: {e.response.status_code}\nResponse: {e.response.text}",
		)
		frappe.throw(_("Failed to list carrier services: {0}").format(e.response.text))
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(title="Error listing carrier services", message=error_msg)
		frappe.throw(_("Failed to list carrier services: {0}").format(error_msg))


@frappe.whitelist()
def get_all_carrier_package_types(settings_name: str | None = None) -> dict[str, list[dict]]:
	"""
	Get package types for all configured carriers.

	Returns:
	        Dict mapping carrier_id to list of package types
	"""
	settings = get_shipstation_settings(settings_name)

	# Get carrier IDs from stored carrier data
	carrier_data = []
	if settings.shipstation_api_carrier_data:
		carrier_data = json.loads(settings.shipstation_api_carrier_data)

	if not carrier_data:
		# Fetch carriers if not cached
		carriers = list_carriers(settings_name)
		carrier_ids = [c["carrier_id"] for c in carriers]
	else:
		carrier_ids = [c.get("carrier_id") for c in carrier_data if c.get("carrier_id")]

	result = {}
	for carrier_id in carrier_ids:
		try:
			packages = list_carrier_package_types(carrier_id, settings_name)
			result[carrier_id] = packages
		except Exception as e:
			# Log but don't fail for individual carriers
			frappe.log_error(
				title=f"Error fetching packages for carrier {carrier_id}",
				message=str(e),
			)
			result[carrier_id] = []

	return result


@frappe.whitelist()
def sync_carrier_package_types(settings_name: str | None = None) -> dict:
	"""
	Sync carrier package types from ShipEngine API and update the settings document.

	This fetches detailed package information for all carriers, updates
	the shipstation_api_carrier_data field with the enhanced package data,
	and creates/updates Shipment Parcel Template records for packages with dimensions.

	Args:
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Dict with sync results including carriers, packages, and templates counts
	"""
	settings = get_shipstation_settings(settings_name)
	api_key = settings.get_password("shipstation_api_key")

	# First fetch all carriers
	client = settings.shipstation_api_client()
	response = client.list_carriers()
	carriers = response.get("carriers", []) if isinstance(response, dict) else response

	carrier_list = []
	total_packages = 0
	templates_created = 0
	templates_updated = 0
	transporters_created = 0

	for carrier in carriers:
		if isinstance(carrier, dict):
			carrier_id = carrier.get("carrier_id")
			carrier_code = carrier.get("carrier_code")
			account_number = carrier.get("account_number")
			name = carrier.get("friendly_name") or carrier.get("nickname")
			services = carrier.get("services", [])
		else:
			carrier_id = getattr(carrier, "carrier_id", None)
			carrier_code = getattr(carrier, "carrier_code", None)
			account_number = getattr(carrier, "account_number", None)
			name = getattr(carrier, "friendly_name", None) or getattr(carrier, "nickname", None)
			services = getattr(carrier, "services", [])

		# Create or find Supplier/transporter for this carrier
		supplier_name = None
		if name:
			existing_before = frappe.db.exists("Supplier", {"supplier_name": name, "is_transporter": 1})
			supplier_name = get_or_create_transporter(name)
			if supplier_name and not existing_before:
				transporters_created += 1

		carrier_data = {
			"carrier_id": carrier_id,
			"carrier_code": carrier_code,
			"account_number": account_number,
			"name": name,
			"supplier": supplier_name,
			"services": [],
			"packages": [],
		}

		# Process services
		for s in services or []:
			if isinstance(s, dict):
				carrier_data["services"].append(
					{
						"service_code": s.get("service_code"),
						"name": s.get("name"),
						"domestic": s.get("domestic"),
						"international": s.get("international"),
					}
				)
			else:
				carrier_data["services"].append(
					{
						"service_code": getattr(s, "service_code", None),
						"name": getattr(s, "name", None),
						"domestic": getattr(s, "domestic", None),
						"international": getattr(s, "international", None),
					}
				)

		# Fetch detailed package types for this carrier
		try:
			with httpx.Client() as http_client:
				pkg_response = http_client.get(
					f"{SHIPENGINE_API_URL}/carriers/{carrier_id}/packages",
					headers={"API-Key": api_key},
				)
				if pkg_response.status_code == 200:
					pkg_data = pkg_response.json()
					packages = pkg_data.get("packages", [])
					for p in packages:
						formatted_pkg = _format_package_type(p)
						carrier_data["packages"].append(formatted_pkg)

						# Create/update Shipment Parcel Template for packages with dimensions
						created, updated = _sync_parcel_template(formatted_pkg, carrier_code, name, supplier_name)
						templates_created += created
						templates_updated += updated

					total_packages += len(packages)
		except Exception as e:
			frappe.log_error(
				title=f"Error fetching packages for carrier {carrier_id}",
				message=str(e),
			)

		carrier_list.append(carrier_data)

	# Update settings with enhanced carrier data
	settings.shipstation_api_carrier_data = json.dumps(carrier_list)
	settings.save()

	result = {
		"carriers_synced": len(carrier_list),
		"total_packages": total_packages,
		"templates_created": templates_created,
		"templates_updated": templates_updated,
		"transporters_created": transporters_created,
		"carriers": [
			{
				"carrier_id": c["carrier_id"],
				"name": c["name"],
				"supplier": c.get("supplier"),
				"packages_count": len(c["packages"]),
			}
			for c in carrier_list
		],
	}

	frappe.msgprint(
		_(
			"Successfully synced {0} carriers with {1} package types. "
			"Created {2} Shipment Parcel Templates, updated {3}. "
			"Created {4} new transporter Suppliers."
		).format(
			result["carriers_synced"],
			result["total_packages"],
			result["templates_created"],
			result["templates_updated"],
			result["transporters_created"],
		)
	)

	return result


def _sync_parcel_template(
	package: dict, carrier_code: str, carrier_name: str, supplier_name: str | None = None
) -> tuple[int, int]:
	"""
	Create or update a Shipment Parcel Template for a carrier package type.

	Creates templates for all packages. Packages without dimension data get
	placeholder dimensions (1x1x1 cm) which should be updated by the user.

	Args:
	        package: Package data dict from _format_package_type
	        carrier_code: Carrier code (e.g., "usps", "ups")
	        carrier_name: Friendly carrier name (e.g., "USPS", "UPS")
	        supplier_name: Optional Supplier (transporter) name to link to the template

	Returns:
	        Tuple of (created_count, updated_count)
	"""
	# Build template name: "USPS - Flat Rate Envelope" or use package code if no name
	package_name = package.get("name") or package.get("package_code", "")
	template_name = f"{carrier_name} - {package_name}".strip(" -")

	if not template_name or template_name == carrier_name:
		return (0, 0)

	# Sanitize name - remove special characters that Frappe doesn't allow
	# Characters like <, >, &, ", ' can cause issues in document names
	template_name = re.sub(r'[<>"\']', "", template_name)
	template_name = template_name.replace("&", "and")
	template_name = template_name.replace("®", "")  # Remove registered trademark symbol
	template_name = re.sub(r"\s+", " ", template_name).strip()  # Normalize whitespace

	# Truncate if too long (field limit is usually 140 chars)
	if len(template_name) > 140:
		template_name = template_name[:137] + "..."

	if not template_name or template_name == carrier_name:
		return (0, 0)

	# Get dimensions - first from API, then from known dimensions lookup, then placeholder
	package_code = package.get("package_code", "")
	dimensions = package.get("dimensions") or {}
	length = dimensions.get("length")
	width = dimensions.get("width")
	height = dimensions.get("height")
	unit = dimensions.get("unit", "inch")

	# Check if we have valid dimensions from API (not None and > 0)
	has_valid_dimensions = all(
		[
			length is not None and length > 0,
			width is not None and width > 0,
			height is not None and height > 0,
		]
	)

	# If no dimensions from API, try the known dimensions lookup
	if not has_valid_dimensions and package_code in KNOWN_PACKAGE_DIMENSIONS:
		known = KNOWN_PACKAGE_DIMENSIONS[package_code]
		length = known["length"]
		width = known["width"]
		height = known["height"]
		unit = known.get("unit", "inch")
		has_valid_dimensions = True

	if has_valid_dimensions:
		# Convert inches to cm if needed (Shipment Parcel Template uses cm)
		if unit in ("inch", "inches", "in"):
			# 1 inch = 2.54 cm
			length = int(round(length * 2.54))
			width = int(round(width * 2.54))
			height = int(round(height * 2.54))
		else:
			length = int(round(length))
			width = int(round(width))
			height = int(round(height))
	else:
		# Use placeholder dimensions for packages without dimension data
		# These should be updated by the user if they want accurate dimensions
		length = 1
		width = 1
		height = 1

	# Check if template exists
	existing = frappe.db.exists("Shipment Parcel Template", template_name)

	try:
		if existing:
			# Update existing template
			doc = frappe.get_doc("Shipment Parcel Template", template_name)
			updated = False

			# Only update dimensions if we have valid dimensions from API
			# Don't overwrite user-customized dimensions with placeholders
			if has_valid_dimensions:
				doc.length = length
				doc.width = width
				doc.height = height
				if not doc.weight:
					doc.weight = 1.0
				updated = True

			# Always update carrier if provided and not already set
			if supplier_name and not doc.carrier:
				doc.carrier = supplier_name
				updated = True

			if updated:
				doc.save(ignore_permissions=True)
				return (0, 1)
			return (0, 0)
		else:
			# Create new template
			doc = frappe.get_doc(
				{
					"doctype": "Shipment Parcel Template",
					"parcel_template_name": template_name,
					"length": length,
					"width": width,
					"height": height,
					"weight": 1.0,  # Default weight since ShipEngine doesn't provide this
					"carrier": supplier_name,  # Link to transporter Supplier
				}
			)
			doc.insert(ignore_permissions=True)
			return (1, 0)
	except Exception as e:
		frappe.log_error(
			title=f"Error syncing parcel template: {template_name}",
			message=str(e),
		)
		return (0, 0)


@frappe.whitelist()
def get_package_type_options(carrier_id: str, settings_name: str | None = None) -> list[dict]:
	"""
	Get package type options formatted for a select field.

	This is a convenience method for populating dropdown options in the UI.

	Args:
	        carrier_id: The ShipEngine carrier ID
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        List of dicts with 'value' and 'label' keys for use in select fields
	"""
	packages = list_carrier_package_types(carrier_id, settings_name)

	options = [{"value": "", "label": _("Custom Package")}]  # Default option
	for pkg in packages:
		label = pkg.get("name", pkg.get("package_code", ""))
		if pkg.get("description"):
			label = f"{label} - {pkg['description']}"
		options.append(
			{
				"value": pkg.get("package_code"),
				"label": label,
			}
		)

	return options


@frappe.whitelist()
def get_carrier_id_for_supplier(
	supplier_name: str, settings_name: str | None = None
) -> str | None:
	"""
	Look up the ShipEngine carrier_id for a given Supplier (transporter) name.

	Uses the synced carrier data stored in Shipstation Settings to map
	Supplier names to ShipEngine carrier IDs.

	Args:
	        supplier_name: The Supplier document name (e.g., "UPS", "USPS")
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        ShipEngine carrier_id (e.g., "se-123456") or None if not found
	"""
	if not supplier_name:
		return None

	settings = get_shipstation_settings(settings_name)

	# Get cached carrier data
	if not settings.shipstation_api_carrier_data:
		frappe.throw(_("No carrier data found. Please sync carriers first."))

	carrier_data = json.loads(settings.shipstation_api_carrier_data)

	# Look up by name (case-insensitive)
	supplier_name_lower = supplier_name.lower()
	for carrier in carrier_data:
		carrier_name = carrier.get("name", "")
		if carrier_name and carrier_name.lower() == supplier_name_lower:
			carrier_id = carrier.get("carrier_id")
			# Validate carrier_id format (should be like "se-123456")
			if carrier_id and carrier_id.startswith("se-"):
				return carrier_id
			frappe.log_error(
				title="Invalid carrier_id format",
				message=f"Carrier '{carrier_name}' has invalid carrier_id: {carrier_id}",
			)
			return None

		# Also check supplier field if it was stored
		carrier_supplier = carrier.get("supplier", "")
		if carrier_supplier and carrier_supplier.lower() == supplier_name_lower:
			carrier_id = carrier.get("carrier_id")
			if carrier_id and carrier_id.startswith("se-"):
				return carrier_id
			frappe.log_error(
				title="Invalid carrier_id format",
				message=f"Carrier with supplier '{carrier_supplier}' has invalid carrier_id: {carrier_id}",
			)
			return None

	# Not found - log for debugging
	available_carriers = [f"{c.get('name')} (supplier: {c.get('supplier')})" for c in carrier_data]
	frappe.log_error(
		title="Carrier not found for supplier",
		message=f"Looking for supplier: {supplier_name}\nAvailable carriers: {', '.join(available_carriers)}",
	)

	return None


@frappe.whitelist()
def get_services_for_supplier(supplier_name: str, settings_name: str | None = None) -> list[dict]:
	"""
	Get available services for a Supplier (transporter).

	Convenience method that looks up the ShipEngine carrier_id for a Supplier
	and returns its available services.

	Args:
	        supplier_name: The Supplier document name (e.g., "UPS", "USPS")
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        List of service dicts with service_code, name, etc.
	"""
	try:
		carrier_id = get_carrier_id_for_supplier(supplier_name, settings_name)
		if not carrier_id:
			# No matching carrier found - return empty list without error
			return []

		return list_carrier_services(carrier_id, settings_name)
	except Exception as e:
		# Don't throw on service lookup failure - just return empty list
		# This prevents page load failures
		frappe.log_error(
			title="Error getting services for supplier",
			message=f"Supplier: {supplier_name}\nError: {str(e)}",
		)
		return []


def _format_carrier(carrier) -> dict:
	"""Format carrier data for consistent output."""
	if isinstance(carrier, dict):
		return {
			"carrier_id": carrier.get("carrier_id"),
			"carrier_code": carrier.get("carrier_code"),
			"account_number": carrier.get("account_number"),
			"name": carrier.get("friendly_name") or carrier.get("nickname"),
			"nickname": carrier.get("nickname"),
			"friendly_name": carrier.get("friendly_name"),
			"primary": carrier.get("primary", False),
			"has_multi_package_supporting_services": carrier.get(
				"has_multi_package_supporting_services", False
			),
			"supports_label_messages": carrier.get("supports_label_messages", False),
			"balance": carrier.get("balance"),
			"supplier": None,  # Will be set by caller if create_transporters=True
		}
	# Handle object response
	return {
		"carrier_id": getattr(carrier, "carrier_id", None),
		"carrier_code": getattr(carrier, "carrier_code", None),
		"account_number": getattr(carrier, "account_number", None),
		"name": getattr(carrier, "friendly_name", None) or getattr(carrier, "nickname", None),
		"nickname": getattr(carrier, "nickname", None),
		"friendly_name": getattr(carrier, "friendly_name", None),
		"primary": getattr(carrier, "primary", False),
		"has_multi_package_supporting_services": getattr(
			carrier, "has_multi_package_supporting_services", False
		),
		"supports_label_messages": getattr(carrier, "supports_label_messages", False),
		"balance": getattr(carrier, "balance", None),
		"supplier": None,  # Will be set by caller if create_transporters=True
	}


def _format_package_type(package: dict) -> dict:
	"""Format package type data for consistent output."""
	dimensions = package.get("dimensions", {})

	return {
		"package_id": package.get("package_id"),
		"package_code": package.get("package_code"),
		"name": package.get("name"),
		"description": package.get("description"),
		"dimensions": {
			"length": dimensions.get("length"),
			"width": dimensions.get("width"),
			"height": dimensions.get("height"),
			"unit": dimensions.get("unit", "inch"),
		}
		if dimensions
		else None,
	}


def _format_service(service: dict) -> dict:
	"""Format service data for consistent output."""
	return {
		"carrier_id": service.get("carrier_id"),
		"carrier_code": service.get("carrier_code"),
		"service_code": service.get("service_code"),
		"name": service.get("name"),
		"domestic": service.get("domestic", False),
		"international": service.get("international", False),
		"is_multi_package_supported": service.get("is_multi_package_supported", False),
	}


@frappe.whitelist()
def get_shipping_accounts(delivery_note):
	dn = frappe.get_doc("Delivery Note", delivery_note)

	accounts = []

	if dn.customer:
		doc = frappe.get_doc("Customer", dn.customer)
		for row in doc.shipping_accounts:  # child table fieldname
			accounts.append(
				{"shipping_account_number": row.shipping_account_number, "carrier": row.carrier}
			)

	elif dn.supplier:
		doc = frappe.get_doc("Supplier", dn.supplier)
		for row in doc.shipping_accounts:
			accounts.append(
				{"shipping_account_number": row.shipping_account_number, "carrier": row.carrier}
			)

	return accounts
