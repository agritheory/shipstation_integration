# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

"""
Fulfillment operations using ShipStation API v2.

This module provides functionality for creating fulfillments and
pushing tracking data back to connected marketplaces.
"""

from typing import TYPE_CHECKING, Optional

import frappe
from frappe import _

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


@frappe.whitelist()
def create_fulfillment(
	delivery_note: str,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Create a fulfillment record in ShipStation API v2.

	This marks an order as fulfilled and pushes tracking information
	back to connected marketplaces (Shopify, Amazon, etc.).

	Args:
		delivery_note: Delivery Note document name
		settings_name: Optional Shipstation Settings document name

	Returns:
		Fulfillment response with fulfillment_id
	"""
	dn = frappe.get_doc("Delivery Note", delivery_note)

	if not dn.tracking_number:
		frappe.throw(_("Delivery Note must have a tracking number to create a fulfillment"))

	effective_settings = settings_name or (dn.integration_doc if dn.integration_doc else None)
	settings = _get_settings(effective_settings)
	client = settings.shipstation_api_client()

	# Get shipping address
	ship_to_address = frappe.get_doc("Address", dn.shipping_address_name) if dn.shipping_address_name else None

	# Build fulfillment payload
	fulfillment_data = {
		"shipment_number": dn.name,
		"tracking_number": dn.tracking_number,
		"carrier_code": (dn.carrier or "").lower().replace(" ", "_"),
		"ship_date": dn.posting_date.isoformat() if dn.posting_date else None,
	}

	# Add ship-to info if available
	if ship_to_address:
		fulfillment_data["ship_to"] = {
			"name": dn.customer_name or dn.customer,
			"address_line1": ship_to_address.address_line1,
			"address_line2": ship_to_address.address_line2 or "",
			"city_locality": ship_to_address.city,
			"state_province": ship_to_address.state,
			"postal_code": ship_to_address.pincode,
			"country_code": (frappe.db.get_value("Country", ship_to_address.country, "code") or "US").upper(),
		}

	# Add items if present
	if dn.items:
		fulfillment_data["items"] = []
		for item in dn.items:
			fulfillment_data["items"].append({
				"name": item.item_name,
				"sku": item.item_code,
				"quantity": int(item.qty),
			})

	# Add order reference if available
	if dn.shipstation_order_id:
		fulfillment_data["order_id"] = dn.shipstation_order_id

	try:
		# Use httpx directly since the SDK may not have fulfillments method
		import httpx

		api_key = settings.get_password("shipstation_api_key")
		headers = {
			"API-Key": api_key,
			"Content-Type": "application/json",
		}

		with httpx.Client() as http_client:
			response = http_client.post(
				"https://api.shipstation.com/v2/fulfillments",
				headers=headers,
				json=fulfillment_data,
				timeout=30,
			)
			response.raise_for_status()
			result = response.json()

		# Store fulfillment ID on delivery note
		if result.get("fulfillment_id"):
			frappe.db.set_value(
				"Delivery Note",
				dn.name,
				"shipstation_fulfillment_id",
				result.get("fulfillment_id"),
			)

		return {
			"fulfillment_id": result.get("fulfillment_id"),
			"status": "success",
			"message": _("Fulfillment created successfully"),
		}

	except Exception as e:
		frappe.log_error(title="Error creating fulfillment", message=str(e))
		frappe.throw(_("Failed to create fulfillment: {0}").format(str(e)))


@frappe.whitelist()
def list_fulfillments(
	filters: Optional[dict] = None,
	page: int = 1,
	page_size: int = 25,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	List fulfillments with optional filters.

	Args:
		filters: Optional filter dict with keys: tracking_number, ship_date_start, ship_date_end, etc.
		page: Page number for pagination
		page_size: Number of results per page
		settings_name: Optional Shipstation Settings document name

	Returns:
		Dict with fulfillments list and pagination info
	"""
	settings = _get_settings(settings_name)

	try:
		import httpx

		api_key = settings.get_password("shipstation_api_key")
		headers = {
			"API-Key": api_key,
		}

		params = {
			"page": page,
			"page_size": page_size,
		}

		if filters:
			if filters.get("tracking_number"):
				params["tracking_number"] = filters["tracking_number"]
			if filters.get("ship_date_start"):
				params["ship_date_start"] = filters["ship_date_start"]
			if filters.get("ship_date_end"):
				params["ship_date_end"] = filters["ship_date_end"]
			if filters.get("shipment_number"):
				params["shipment_number"] = filters["shipment_number"]

		with httpx.Client() as http_client:
			response = http_client.get(
				"https://api.shipstation.com/v2/fulfillments",
				headers=headers,
				params=params,
				timeout=30,
			)
			response.raise_for_status()
			result = response.json()

		return {
			"fulfillments": result.get("fulfillments", []),
			"total": result.get("total", 0),
			"page": result.get("page", page),
			"pages": result.get("pages", 1),
		}

	except Exception as e:
		frappe.log_error(title="Error listing fulfillments", message=str(e))
		frappe.throw(_("Failed to list fulfillments: {0}").format(str(e)))


@frappe.whitelist()
def get_fulfillment(
	fulfillment_id: str,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Get a specific fulfillment by ID.

	Args:
		fulfillment_id: The fulfillment ID
		settings_name: Optional Shipstation Settings document name

	Returns:
		Fulfillment details dict
	"""
	settings = _get_settings(settings_name)

	try:
		import httpx

		api_key = settings.get_password("shipstation_api_key")
		headers = {
			"API-Key": api_key,
		}

		# List with fulfillment_id filter
		with httpx.Client() as http_client:
			response = http_client.get(
				"https://api.shipstation.com/v2/fulfillments",
				headers=headers,
				params={"fulfillment_id": fulfillment_id},
				timeout=30,
			)
			response.raise_for_status()
			result = response.json()

		fulfillments = result.get("fulfillments", [])
		if fulfillments:
			return fulfillments[0]

		frappe.throw(_("Fulfillment not found"))

	except Exception as e:
		frappe.log_error(title="Error fetching fulfillment", message=str(e))
		frappe.throw(_("Failed to fetch fulfillment: {0}").format(str(e)))


@frappe.whitelist()
def sync_fulfillment_from_delivery_notes(
	delivery_notes: Optional[list[str]] = None,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Sync fulfillments for multiple Delivery Notes.

	Args:
		delivery_notes: Optional list of Delivery Note names. If not provided,
						syncs all submitted DNs with tracking numbers but no fulfillment ID.
		settings_name: Optional Shipstation Settings document name

	Returns:
		Summary of sync results
	"""
	if not delivery_notes:
		# Get all submitted DNs with tracking but no fulfillment
		delivery_notes = frappe.get_all(
			"Delivery Note",
			filters={
				"docstatus": 1,
				"tracking_number": ["is", "set"],
				"shipstation_fulfillment_id": ["is", "not set"],
			},
			pluck="name",
			limit=100,
		)

	results = {
		"success": [],
		"failed": [],
		"skipped": [],
	}

	for dn_name in delivery_notes:
		try:
			dn = frappe.get_doc("Delivery Note", dn_name)

			# Skip if already has fulfillment
			if dn.get("shipstation_fulfillment_id"):
				results["skipped"].append(dn_name)
				continue

			# Skip if no tracking
			if not dn.tracking_number:
				results["skipped"].append(dn_name)
				continue

			create_fulfillment(dn_name, settings_name)
			results["success"].append(dn_name)

		except Exception as e:
			results["failed"].append({"name": dn_name, "error": str(e)})

	return results


def _get_settings(settings_name: Optional[str] = None) -> "ShipstationSettings":
	"""Get Shipstation Settings document."""
	if settings_name:
		return frappe.get_doc("Shipstation Settings", settings_name)

	settings_list = frappe.get_all(
		"Shipstation Settings",
		filters={"enabled": 1, "enable_shipstation_api": 1},
		limit=1,
	)

	if not settings_list:
		frappe.throw(_("No Shipstation Settings found with ShipStation API v2 enabled"))

	return frappe.get_doc("Shipstation Settings", settings_list[0].name)

