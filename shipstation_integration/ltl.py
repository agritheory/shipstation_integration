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
from frappe import _
from frappe.utils.file_manager import save_file

from shipstation_integration.carriers import (
	_get_error_message,
	_get_settings,
	get_or_create_transporter,
)


@frappe.whitelist()
def list_ltl_carriers(
	settings_name: str | None = None, create_transporters: bool = False
) -> list[dict]:
	"""
	List all LTL carriers connected to the ShipStation account.

	Args:
	settings_name: Optional Shipstation Settings document name
	create_transporters: If True, create Supplier records with is_transporter=1
	for each carrier that doesn't already exist

	Returns:
	List of carrier dicts with carrier_id, carrier_code, name, supplier, etc.
	"""
	settings = _get_settings(settings_name)
	carriers = settings.list_ltl_carriers()
	result = []
	for c in carriers:
		formatted = _format_ltl_carrier(c)

		# Optionally create transporter Supplier
		if create_transporters and formatted.get("name"):
			supplier_name = get_or_create_transporter(formatted["name"])
			formatted["supplier"] = supplier_name

		result.append(formatted)

	return result


@frappe.whitelist()
def get_ltl_carrier(carrier_id: str, settings_name: str | None = None) -> dict:
	"""
	Get details for a specific LTL carrier.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name

	Returns:
	LTL carrier details dict
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

	try:
		with httpx.Client() as client:
			response = client.get(
				f"{base_url}/v-beta/ltl/carriers/{carrier_id}",
				headers=headers,
			)
			data = response.json()
			response.raise_for_status()
			return _format_ltl_carrier(data)
	except httpx.HTTPStatusError as e:
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error getting carrier", message=error_msg)
		frappe.throw(_("Failed to get carrier: {0}").format(error_msg))


@frappe.whitelist()
def list_ltl_carrier_features(carrier_id: str, settings_name: str | None = None) -> list[str]:
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
	carrier_data = get_ltl_carrier(carrier_id=carrier_id, settings_name=settings_name)
	return carrier_data.get("features", [])


@frappe.whitelist()
def list_ltl_carrier_documents(
	carrier_id: str, pro_number: str, settings_name: str | None = None
) -> list[dict]:
	"""
	List all documents from an LTL carrier associated with a specific PRO number. The documents
	that can be associated with a PRO number are "bill_of_lading", "delivery_receipt", "invoice",
	or "weight_inspection_certificate".

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	pro_number: The PRO number is equivalent to the tracking number in parcel shipping
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of LTL carrier document dicts, each with the type (options noted above), image (base64-
	encoded bill of lading document), and format (always PDF for pickup responses, the format the
	image will be in once decoded)
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

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
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error listing LTL carrier documents", message=error_msg)
		frappe.throw(_("Failed to list LTL carrier documents: {0}").format(error_msg))


@frappe.whitelist()
def list_ltl_carrier_options(carrier_id: str, settings_name: str | None = None) -> list[dict]:
	"""
	List all options aka accessorial services (e.g. Hazardous Material, Perishable, Inside Pickup)
	for a specific LTL carrier.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of LTL carrier options dicts with attributes, code, features, and name for each option
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

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
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error listing LTL carrier options", message=error_msg)
		frappe.throw(_("Failed to list LTL carrier options: {0}").format(error_msg))


@frappe.whitelist()
def list_ltl_carrier_package_types(
	carrier_id: str, settings_name: str | None = None
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
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

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
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error listing LTL carrier package/container types", message=error_msg)
		frappe.throw(_("Failed to list LTL carrier package/container types: {0}").format(error_msg))


@frappe.whitelist()
def get_all_ltl_carrier_package_types(settings_name: str | None = None) -> dict[str, list[dict]]:
	"""
	Get package types for all configured LTL carriers.

	Args:
	settings_name: Optional Shipstation Settings document name

	Returns:
	Dict mapping carrier_id to list of package types
	"""
	settings = _get_settings(settings_name)

	# Get carrier IDs from stored LTL carrier data
	carrier_data = []
	if settings.shipstation_api_ltl_carrier_data:
		carrier_data = json.loads(settings.shipstation_api_ltl_carrier_data)

	if not carrier_data:
		# Fetch carriers if not cached
		carriers = list_ltl_carriers(settings_name)
		carrier_ids = [c["carrier_id"] for c in carriers]
	else:
		carrier_ids = [c.get("carrier_id") for c in carrier_data if c.get("carrier_id")]

	result = {}
	for carrier_id in carrier_ids:
		try:
			packages = list_ltl_carrier_package_types(carrier_id, settings_name)
			result[carrier_id] = packages
		except Exception as e:
			# Log but don't fail for individual carriers
			frappe.log_error(
				title=f"Error fetching packages for LTL carrier {carrier_id}",
				message=str(e),
			)
			result[carrier_id] = []

	return result


@frappe.whitelist()
def list_ltl_carrier_services(carrier_id: str, settings_name: str | None = None) -> list[dict]:
	"""
	List all service levels (e.g. Guaranteed Morning, Guaranteed Noon, Standard) for a specific
	LTL carrier.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of LTL carrier service dicts with code, features, and name for each option
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

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
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error listing LTL carrier options", message=error_msg)
		frappe.throw(_("Failed to list LTL carrier options: {0}").format(error_msg))


# @frappe.whitelist()
# def sync_ltl_carrier_package_types(settings_name: str | None = None) -> dict:
# 	"""
# 	Sync carrier package types from ShipEngine API and update the settings document.

# 	This fetches detailed package information for all carriers, updates
# 	the shipstation_api_ltl_carrier_data field with the enhanced package data,
# 	and creates/updates Shipment Parcel Template records for packages with dimensions.

# 	Args:
# 	settings_name: Optional Shipstation Settings document name

# 	Returns:
# 	Dict with sync results including carriers, packages, and templates counts
# 	"""
# 	settings = _get_settings(settings_name)
# 	api_key = settings.get_password("shipstation_api_key")

# 	# First fetch all carriers
# 	client = settings.shipstation_api_client()
# 	response = client.list_carriers()
# 	carriers = response.get("carriers", []) if isinstance(response, dict) else response

# 	carrier_list = []
# 	total_packages = 0
# 	templates_created = 0
# 	templates_updated = 0
# 	transporters_created = 0

# 	for carrier in carriers:
# 		if isinstance(carrier, dict):
# 			carrier_id = carrier.get("carrier_id")
# 			carrier_code = carrier.get("carrier_code")
# 			account_number = carrier.get("account_number")
# 			name = carrier.get("friendly_name") or carrier.get("nickname")
# 			services = carrier.get("services", [])
# 		else:
# 			carrier_id = getattr(carrier, "carrier_id", None)
# 			carrier_code = getattr(carrier, "carrier_code", None)
# 			account_number = getattr(carrier, "account_number", None)
# 			name = getattr(carrier, "friendly_name", None) or getattr(carrier, "nickname", None)
# 			services = getattr(carrier, "services", [])

# 		# Create or find Supplier/transporter for this carrier
# 		supplier_name = None
# 		if name:
# 			existing_before = frappe.db.exists("Supplier", {"supplier_name": name, "is_transporter": 1})
# 			supplier_name = get_or_create_transporter(name)
# 			if supplier_name and not existing_before:
# 				transporters_created += 1

# 		carrier_data = {
# 			"carrier_id": carrier_id,
# 			"carrier_code": carrier_code,
# 			"account_number": account_number,
# 			"name": name,
# 			"supplier": supplier_name,
# 			"services": [],
# 			"packages": [],
# 		}

# 		# Process services
# 		for s in services or []:
# 			if isinstance(s, dict):
# 				carrier_data["services"].append(
# 					{
# 						"service_code": s.get("service_code"),
# 						"name": s.get("name"),
# 						"domestic": s.get("domestic"),
# 						"international": s.get("international"),
# 					}
# 				)
# 			else:
# 				carrier_data["services"].append(
# 					{
# 						"service_code": getattr(s, "service_code", None),
# 						"name": getattr(s, "name", None),
# 						"domestic": getattr(s, "domestic", None),
# 						"international": getattr(s, "international", None),
# 					}
# 				)

# 		# Fetch detailed package types for this carrier
# 		try:
# 			with httpx.Client() as http_client:
# 				pkg_response = http_client.get(
# 					f"{SHIPENGINE_API_URL}/carriers/{carrier_id}/packages",
# 					headers={"API-Key": api_key},
# 				)
# 				if pkg_response.status_code == 200:
# 					pkg_data = pkg_response.json()
# 					packages = pkg_data.get("packages", [])
# 					for p in packages:
# 						formatted_pkg = _format_package_type(p)
# 						carrier_data["packages"].append(formatted_pkg)

# 						# Create/update Shipment Parcel Template for packages with dimensions
# 						created, updated = _sync_parcel_template(formatted_pkg, carrier_code, name, supplier_name)
# 						templates_created += created
# 						templates_updated += updated

# 					total_packages += len(packages)
# 		except Exception as e:
# 			frappe.log_error(
# 				title=f"Error fetching packages for carrier {carrier_id}",
# 				message=str(e),
# 			)

# 		carrier_list.append(carrier_data)

# 	# Update settings with enhanced carrier data
# 	settings.shipstation_api_carrier_data = json.dumps(carrier_list)
# 	settings.save()

# 	result = {
# 		"carriers_synced": len(carrier_list),
# 		"total_packages": total_packages,
# 		"templates_created": templates_created,
# 		"templates_updated": templates_updated,
# 		"transporters_created": transporters_created,
# 		"carriers": [
# 			{
# 				"carrier_id": c["carrier_id"],
# 				"name": c["name"],
# 				"supplier": c.get("supplier"),
# 				"packages_count": len(c["packages"]),
# 			}
# 			for c in carrier_list
# 		],
# 	}

# 	frappe.msgprint(
# 		_(
# 			"Successfully synced {0} carriers with {1} package types. "
# 			"Created {2} Shipment Parcel Templates, updated {3}. "
# 			"Created {4} new transporter Suppliers."
# 		).format(
# 			result["carriers_synced"],
# 			result["total_packages"],
# 			result["templates_created"],
# 			result["templates_updated"],
# 			result["transporters_created"],
# 		)
# 	)

# 	return result


@frappe.whitelist()
def get_ltl_package_type_options(carrier_id: str, settings_name: str | None = None) -> list[dict]:
	"""
	Get package type options formatted for a select field.

	This is a convenience method for populating dropdown options in the UI.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of dicts with 'value' and 'label' keys for use in select fields
	"""
	packages = list_ltl_carrier_package_types(carrier_id, settings_name)

	options = []
	for pkg in packages:
		label = pkg.get("name", pkg.get("code", ""))
		options.append(
			{
				"value": pkg.get("code"),
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

	Uses the synced LTL carrier data stored in Shipstation Settings to map
	Supplier names to ShipEngine carrier IDs.

	Args:
	supplier_name: The Supplier document name (e.g., "UPS", "USPS")
	settings_name: Optional Shipstation Settings document name

	Returns:
	ShipEngine carrier_id (e.g., (e.g., "100abcde-...")) or None if not found
	"""
	if not supplier_name:
		return None

	settings = _get_settings(settings_name)
	id_pattern = re.compile(
		"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
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


@frappe.whitelist()
def get_ltl_services_for_supplier(
	supplier_name: str, settings_name: str | None = None
) -> list[dict]:
	"""
	Get available services for a Supplier (transporter).

	Convenience method that looks up the ShipEngine carrier_id for a Supplier and returns its
	available services.

	Args:
	supplier_name: The Supplier document name (e.g., "UPS", "USPS")
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of service dicts with code, features, and name
	"""
	try:
		carrier_id = get_carrier_id_for_supplier(supplier_name, settings_name)
		if not carrier_id:
			# No matching carrier found - return empty list without error
			return []

		return list_ltl_carrier_services(carrier_id, settings_name)
	except Exception as e:
		# Don't throw on service lookup failure - just return empty list
		# This prevents page load failures
		frappe.log_error(
			title="Error getting LTL services for supplier",
			message=f"Supplier: {supplier_name}\nError: {str(e)}",
		)
		return []


@frappe.whitelist()
def get_ltl_options_for_supplier(
	supplier_name: str, settings_name: str | None = None
) -> list[dict]:
	"""
	Get available options aka accessorial services for a Supplier (transporter).

	Convenience method that looks up the ShipEngine carrier_id for a Supplier
	and returns its available options.

	Args:
	supplier_name: The Supplier document name (e.g., "UPS", "USPS")
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of options dicts with code, features, and name
	"""
	try:
		carrier_id = get_carrier_id_for_supplier(supplier_name, settings_name)
		if not carrier_id:
			# No matching carrier found - return empty list without error
			return []

		return list_ltl_carrier_options(carrier_id, settings_name)
	except Exception as e:
		# Don't throw on service lookup failure - just return empty list
		# This prevents page load failures
		frappe.log_error(
			title="Error getting LTL options for supplier",
			message=f"Supplier: {supplier_name}\nError: {str(e)}",
		)
		return []


@frappe.whitelist()
def does_ltl_carrier_support_quote_or_spot_quote(
	carrier_id: str, settings_name: str | None = None
) -> dict:
	"""
	Convenience function that returns True/False whether a given carrier code supports requesting
	quotes and spot quotes.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name

	Returns:
	Dict with keys for "supports_quote" and "supports_spot_quote" with boolean values
	"""
	feats = list_ltl_carrier_features(carrier_id == carrier_id, settings_name=settings_name)
	return {"supports_quote": "quote" in feats, "supports_spot_quote": "spot_quote" in feats}


@frappe.whitelist()
def does_ltl_carrier_support_scheduled_pickup(
	carrier_id: str, settings_name: str | None = None
) -> dict:
	"""
	Convenience function that returns True/False whether a given carrier code supports
	electronically scheduling a pickup.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name

	Returns:
	Dict with key for "supports_pickup" with boolean value
	"""
	feats = list_ltl_carrier_features(carrier_id == carrier_id, settings_name=settings_name)
	return {"supports_pickup": "scheduled_pickup" in feats}


@frappe.whitelist()
def request_ltl_quote(carrier_id: str, doc: Shipment, settings_name: str | None = None) -> dict:
	"""
	Gets LTL quote given a specific LTL carrier and shipment data.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	doc: a Shipment document in ERPNext from which to retrieve the shipment info
	settings_name: Optional Shipstation Settings document name

	Returns:
	ShipEngine quote dict, includes quote_id, charges list of dicts, and shipment info
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

	try:
		with httpx.Client() as client:
			post_data = {
				"carrier_id": carrier_id,
				"shipment": get_shipment_object_from_doc(doc=doc),
				"shipment_measurements": get_shipment_measurements_object_from_doc(doc=doc),
			}
			response = client.post(
				f"{base_url}/v-beta/ltl/quotes/{carrier_id}", headers=headers, data=json.dumps(post_data)
			)
			data = response.json()
			response.raise_for_status()
			return data

	except httpx.HTTPStatusError as e:
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error getting LTL quote", message=error_msg)
		frappe.throw(_("Failed to get LTL quote: {0}").format(error_msg))


@frappe.whitelist()
def request_ltl_spot_quote(
	carrier_id: str, doc: Shipment, settings_name: str | None = None
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
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

	try:
		with httpx.Client() as client:
			post_data = {
				"carrier_id": carrier_id,
				"shipment": get_shipment_object_from_doc(doc=doc, is_spot_quote=True),
				"shipment_measurements": get_shipment_measurements_object_from_doc(doc=doc),
			}
			response = client.post(
				f"{base_url}/v-beta/ltl/spot-quotes/{carrier_id}", headers=headers, data=json.dumps(post_data)
			)
			data = response.json()
			response.raise_for_status()
			return data.get("quotes", [])

	except httpx.HTTPStatusError as e:
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error getting LTL spot quote", message=error_msg)
		frappe.throw(_("Failed to get LTL spot quote: {0}").format(error_msg))


@frappe.whitelist()
def schedule_ltl_pickup(carrier_id: str, doc: Shipment, settings_name: str | None = None) -> dict:
	"""
	Schedules an LTL pickup with a specific LTL carrier and shipment data.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	doc: a Shipment document in ERPNext from which to retrieve the shipment info
	settings_name: Optional Shipstation Settings document name

	Returns:
	ShipEngine LTL scheduled pickup dict, includes confirmation_number, pro_number, documents
	(Base64-encoded BOL, which decodes to PDF format), pickup_id, and shipment_id among other
	information
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

	try:
		with httpx.Client() as client:
			post_data = {
				"carrier_id": carrier_id,
				"carrier": {  # optional
					"instructions": "",  # TODO: get from doc, if given
					"test": False,  # whether or not this is a test call
				},
				"options": [],  # TODO: optional, get from doc [multiselect?]
				"reference_identifiers": [  # TODO: optional, get from doc
					{
						"type": "",  # may be "bill_of_lading", "pro", "quote", "purchase_order", or "other"
						"value": "",  # string of value provided by carrier
					}
				],
				"shipment": get_shipment_object_from_doc(doc=doc, for_pickup_no_quote=True),
			}
			response = client.post(
				f"{base_url}/v-beta/ltl/pickups/{carrier_id}", headers=headers, data=json.dumps(post_data)
			)
			data = response.json()
			response.raise_for_status()
			return data

	except httpx.HTTPStatusError as e:
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error scheduling LTL pickup", message=error_msg)
		frappe.throw(_("Failed to schedule LTL pickup: {0}").format(error_msg))


@frappe.whitelist()
def schedule_ltl_pickup_with_quote_id(
	quote_id: str, doc: Shipment, settings_name: str | None = None
) -> dict:
	"""
	Schedules an LTL pickup using a ShipEngine quote ID (may be a quote or spot quote ID).

	Args:
	quote_id: The ShipEngine quote ID (which is found in the response from request_ltl_quote or
	request_ltl_spot_quote) - this is not the carrier_quote_id
	doc: a Shipment document in ERPNext from which to retrieve the shipment info
	settings_name: Optional Shipstation Settings document name

	Returns:
	ShipEngine LTL scheduled pickup dict, includes confirmation_number, pro_number, documents
	(Base64-encoded BOL, which decodes to PDF format), pickup_id, and shipment_id among other
	information
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

	try:
		with httpx.Client() as client:
			post_data = {
				"quote_id": quote_id,
				"pickup_date": "",  # TODO: get from doc, YYYY-MM-DD format
				"pickup_window": get_shipment_pickup_window_from_doc(doc=doc),
				"delivery_date": "",  # TODO: get from doc? YYYY-MM-DD format
				"carrier": {  # optional
					"instructions": "",  # TODO: get from doc, if given
					"test": False,  # whether or not this is a test call
				},
				"options": [],  # TODO: get from doc [multiselect?]
			}
			response = client.post(
				f"{base_url}/v-beta/ltl/quotes/{quote_id}/pickup", headers=headers, data=json.dumps(post_data)
			)
			data = response.json()
			response.raise_for_status()
			return data  # TODO: decode BOL and save/attach?

	except httpx.HTTPStatusError as e:
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error scheduling LTL pickup with quote ID", message=error_msg)
		frappe.throw(_("Failed to schedule LTL pickup with quote ID: {0}").format(error_msg))


@frappe.whitelist()
def get_bol_with_pickup_id(
	pickup_id: str, carrier_instructions: str | None = None, settings_name: str | None = None
) -> dict:
	"""
	Gets the Bill of Lading (BOL) for a scheduled pickup using the ShipEngine pickup ID.

	Args:
	pickuo_id: The ShipEngine pickup ID returned after scheduling a pickup
	carrier_instructions: additional instructions for the carrier
	settings_name: Optional Shipstation Settings document name

	Returns:
	ShipEngine dict with type (value will be "bill_of_lading"), image (value is the base64-encoded
	BOL document), and format (value will be "pdf") for the shipment
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

	try:
		with httpx.Client() as client:
			post_data = {"carrier_instructions": carrier_instructions or ""}
			response = client.post(
				f"{base_url}/v-beta/ltl/pickups/{pickup_id}/bill_of_lading",
				headers=headers,
				data=json.dumps(post_data),
			)
			data = response.json()
			response.raise_for_status()
			return data  # TODO: decode document and save/attach?

	except httpx.HTTPStatusError as e:
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error getting BOL using pickup ID", message=error_msg)
		frappe.throw(_("Failed to get BOL using pickup ID: {0}").format(error_msg))


@frappe.whitelist()
def get_bol_with_quote_id(
	quote_id: str, carrier_instructions: str | None = None, settings_name: str | None = None
) -> dict:
	"""
	Gets the Bill of Lading (BOL) for a scheduled pickup using the ShipEngine quote/spot quote ID.

	Args:
	quote_id: The ShipEngine quote ID or spot quote ID for a shipment
	carrier_instructions: additional instructions for the carrier
	settings_name: Optional Shipstation Settings document name

	Returns:
	ShipEngine dict with type (value will be "bill_of_lading"), image (value is the base64-encoded
	BOL document), and format (value will be "pdf") for the shipment
	"""
	settings = _get_settings(settings_name)
	base_url, headers = settings.get_base_url_and_headers()

	try:
		with httpx.Client() as client:
			post_data = {"carrier_instructions": carrier_instructions or ""}
			response = client.post(
				f"{base_url}/v-beta/ltl/quote/{quote_id}/bill_of_lading",
				headers=headers,
				data=json.dumps(post_data),
			)
			data = response.json()
			response.raise_for_status()
			return data  # TODO: decode document and save/attach?

	except httpx.HTTPStatusError as e:
		err_type, err_msg = settings.get_api_response_error_info(data)
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
		error_msg = _get_error_message(e)
		frappe.log_error(title="Error getting BOL using quote ID", message=error_msg)
		frappe.throw(_("Failed to get BOL using quote ID: {0}").format(error_msg))


def _format_ltl_carrier(carrier) -> dict:
	"""Format LTL carrier data for consistent output."""
	carrier["carrier_code"] = carrier.get("scac")
	for p in carrier.get("packages", []):
		p["package_features"] = ", ".join(p.get("features", []))

	return carrier


def get_shipment_object_from_doc(doc: Shipment, for_pickup_no_quote: bool = False) -> dict:
	"""
	Collects necessary data from a Shipment document in ERPNext to populate a shipment object

	Args:
	doc: a Shipment document in ERPNext from which to retrieve the shipment info
	for_pickup_no_quote: if shipment object will be used to directly schedule a pickup without
	first requesting a quote/spot quote. If True, the object includes additional pickup fields

	Returns:
	shipment object dict that may be used to get a quote, spot quote or schedule a pickup
	"""
	# TODO: pull all required data from Shipment
	packages = [  # need an object for each package in shipment
		{
			"code": "",  # ShipEngine package type code
			"freight_class": 0,  # NMFC freight class for the freight (50-500)
			"density": {"value": 0, "unit": "lb/ft3"},  # only unit currently supported
			"nmfc_code": "",  # NMFC commodity code / item number
			"description": "",  # description of what's in container
			"dimensions": {
				"width": 0,
				"height": 0,
				"length": 0,
				"unit": "",
			},  # unit is "inches" or "centimeters"
			"weight": {"value": 0, "unit": ""},  # unit is "grams", "kilograms", "ounces", or "pounds"
			"quantity": 0,  # number of packages of this type
			"stackable": True,  # Boolean whether can be safely stacked or not
			"hazardous_materials": False,  # Boolean whether package contains hazardous materials or not
		}
	]

	options = [
		{"code": "", "attributes": {}},  # attributes is optional, depends on the option
	]

	ship_from = {
		"account": "",  # optional, include if have account number with carrier for quote
		"address": {
			"company_name": "",
			"address_line1": "",
			"address_line2": "",
			"address_line3": "",
			"city_locality": "",
			"state_province": "",
			"postal_code": "",
			"country_code": "",  # The two letter ISO 3166-1 alpha-2 country code
			"residential": False,  # defaults to False
		},
		"contact": {
			"name": "",
			"phone_number": "",
			"email": "",
		},
	}

	ship_to = {
		"account": None,
		"address": {
			"company_name": "",
			"address_line1": "",
			"address_line2": "",
			"address_line3": "",
			"city_locality": "",
			"state_province": "",
			"postal_code": "",
			"country_code": "",
			"residential": False,  # defaults to False
		},
		"contact": {
			"name": "",
			"phone_number": "",
			"email": "",
		},
	}

	bill_to = {
		"type": "",  # may be "consignee", "shipper", or "third_party"
		"payment_terms": "",  # may be "collect", "prepaid", or "third_party"
		"account": "",  # account number to bill for pickup
		"address": {
			"company_name": "",
			"address_line1": "",
			"address_line2": "",
			"address_line3": "",
			"city_locality": "",
			"state_province": "",
			"postal_code": "",
			"country_code": "",
			"residential": False,  # defaults to False
		},
		"contact": {
			"name": "",
			"phone_number": "",
			"email": "",
		},
	}

	requested_by = {
		"company_name": "",
		"contact": {
			"name": "",
			"phone_number": "",
			"email": "",
		},
	}

	shipment_object = {
		"service_code": doc.get("carrier_service", "stnd"),
		"pickup_date": "",  # string in YYYY-MM-DD format
		"packages": packages,  # array of package objects
		"options": options,  # any accessorial services
		"ship_from": ship_from,
		"ship_to": ship_to,
		"bill_to": bill_to,
		"requested_by": requested_by,
	}

	if for_pickup_no_quote:
		shipment_object.update(
			{"delivery_date": "", "pickup_window": get_shipment_pickup_window_from_doc(doc=doc)}
		)

	return shipment_object


def get_shipment_measurements_object_from_doc(doc: Shipment) -> dict:
	"""
	Collects necessary data from a Shipment document in ERPNext to populate a shipment object

	Args:
	doc: a Shipment document in ERPNext from which to retrieve the shipment info

	Returns:
	shipment object dict that may be used in a get quote or get spot quote request
	"""
	# TODO: pull all required data from Shipment
	return {
		"total_linear_length": {"value": 0, "unit": ""},  # unit is "inches" or "centimeters"
		"total_width": {"value": 0, "unit": ""},  # unit is "inches" or "centimeters"
		"total_height": {"value": 0, "unit": ""},  # unit is "inches" or "centimeters"
		"total_weight": {"value": 0, "unit": ""},  # unit is "grams", "kilograms", "ounces", or "pounds"
	}


def get_shipment_pickup_window_from_doc(doc: Shipment) -> dict:
	"""
	Collects necessary data from a Shipment document in ERPNext to populate a pickup window object

	Args:
	doc: a Shipment document in ERPNext from which to retrieve the pickup info

	Returns:
	pickup window object dict that may be used in a scheduled pickup request
	"""
	# TODO: pull all required data from Shipment
	return {  # all fields required, must be in 24 Hour Format: HH:MM:SS, HH:MM:SSZ, HH:MM:SS+/-HH:MM
		"start_at": "",  # earliest time freight will be ready
		"end_at": "",  # latest desired pickup time
		"closing_at": "",  # time the pickup facility closes for business
	}
