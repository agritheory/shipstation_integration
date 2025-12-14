# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

"""
Direct label operations using ShipStation API v2.

This module provides functionality for purchasing, voiding, and
retrieving shipping labels directly through the API.
"""

import base64
from io import BytesIO
from typing import TYPE_CHECKING, Optional

import frappe
from frappe import _
from frappe.utils.file_manager import save_file

if TYPE_CHECKING:
	from frappe.core.doctype.file.file import File

	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


@frappe.whitelist()
def create_label(
	shipment_data: dict,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Purchase a shipping label directly.

	Args:
		shipment_data: Shipment dict with ship_to, ship_from, packages, carrier_id, service_code
		settings_name: Optional Shipstation Settings document name

	Returns:
		Label response with label_id, tracking_number, label_download URL
	"""
	settings = _get_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		label_response = client.create_label_from_shipment(shipment_data)
		return _format_label_response(label_response)
	except Exception as e:
		frappe.log_error(title="Error creating shipping label", message=str(e))
		frappe.throw(_("Failed to create shipping label: {0}").format(str(e)))


@frappe.whitelist()
def create_label_from_rate(
	rate_id: str,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Purchase a shipping label using a pre-selected rate.

	Args:
		rate_id: Rate ID from a previous rate request
		settings_name: Optional Shipstation Settings document name

	Returns:
		Label response with label_id, tracking_number, label_download URL
	"""
	settings = _get_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		label_response = client.create_label_from_rate(rate_id=rate_id)
		return _format_label_response(label_response)
	except Exception as e:
		frappe.log_error(title="Error creating label from rate", message=str(e))
		frappe.throw(_("Failed to create label from rate: {0}").format(str(e)))


@frappe.whitelist()
def void_label(
	label_id: str,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Void a previously purchased label.

	Args:
		label_id: The label ID to void
		settings_name: Optional Shipstation Settings document name

	Returns:
		Void response with status
	"""
	settings = _get_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		void_response = client.void_label(label_id=label_id)
		return {
			"approved": void_response.get("approved", False),
			"message": void_response.get("message", ""),
		}
	except Exception as e:
		frappe.log_error(title="Error voiding label", message=str(e))
		frappe.throw(_("Failed to void label: {0}").format(str(e)))


@frappe.whitelist()
def get_label(
	label_id: str,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Retrieve label details by ID.

	Args:
		label_id: The label ID to retrieve
		settings_name: Optional Shipstation Settings document name

	Returns:
		Label details dict
	"""
	settings = _get_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		label = client.get_label_by_id(label_id=label_id)
		return _format_label_response(label)
	except Exception as e:
		frappe.log_error(title="Error fetching label", message=str(e))
		frappe.throw(_("Failed to fetch label: {0}").format(str(e)))


@frappe.whitelist()
def create_label_for_delivery_note(
	delivery_note: str,
	rate_id: Optional[str] = None,
	carrier_id: Optional[str] = None,
	service_code: Optional[str] = None,
) -> dict:
	"""
	Create a shipping label for a Delivery Note using ShipStation API v2.

	Args:
		delivery_note: Delivery Note document name
		rate_id: Optional rate ID from previous rate request
		carrier_id: Carrier ID (required if rate_id not provided)
		service_code: Service code (required if rate_id not provided)

	Returns:
		Label response and attached file info
	"""
	dn = frappe.get_doc("Delivery Note", delivery_note)

	if not dn.shipping_address_name:
		frappe.throw(_("Delivery Note must have a shipping address"))

	# Get settings
	settings_name = None
	if dn.integration_doctype == "Shipstation Settings" and dn.integration_doc:
		settings_name = dn.integration_doc

	settings = _get_settings(settings_name)

	# If rate_id provided, use it directly
	if rate_id:
		label_response = create_label_from_rate(rate_id=rate_id, settings_name=settings_name)
	else:
		if not carrier_id or not service_code:
			frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))

		# Build shipment data
		shipment_data = _build_shipment_from_delivery_note(dn, carrier_id, service_code)
		label_response = create_label(shipment_data=shipment_data, settings_name=settings_name)

	# Download and attach label PDF
	if label_response.get("label_download"):
		file_doc = _download_and_attach_label(
			label_response["label_download"],
			dn.doctype,
			dn.name,
			settings,
		)
		label_response["attached_file"] = file_doc.name if file_doc else None

	# Update Delivery Note with tracking info
	frappe.db.set_value(dn.doctype, dn.name, {
		"shipstation_shipment_id": label_response.get("shipment_id"),
		"tracking_number": label_response.get("tracking_number"),
		"carrier": label_response.get("carrier_code", "").upper(),
		"carrier_service": label_response.get("service_code", "").upper(),
	})

	return label_response


@frappe.whitelist()
def create_return_label(
	label_id: str,
	settings_name: Optional[str] = None,
) -> dict:
	"""
	Create a return label for a previously created outbound label.

	Args:
		label_id: The original outbound label ID
		settings_name: Optional Shipstation Settings document name

	Returns:
		Return label response
	"""
	settings = _get_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		return_response = client.create_return_label(label_id=label_id)
		return _format_label_response(return_response)
	except Exception as e:
		frappe.log_error(title="Error creating return label", message=str(e))
		frappe.throw(_("Failed to create return label: {0}").format(str(e)))


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


def _format_label_response(label_response) -> dict:
	"""Format label response for frontend consumption."""
	if isinstance(label_response, dict):
		label_download = label_response.get("label_download", {})
		if isinstance(label_download, dict):
			pdf_url = label_download.get("pdf") or label_download.get("href")
		else:
			pdf_url = label_download

		return {
			"label_id": label_response.get("label_id"),
			"shipment_id": label_response.get("shipment_id"),
			"carrier_id": label_response.get("carrier_id"),
			"carrier_code": label_response.get("carrier_code"),
			"service_code": label_response.get("service_code"),
			"tracking_number": label_response.get("tracking_number"),
			"ship_date": label_response.get("ship_date"),
			"created_at": label_response.get("created_at"),
			"shipment_cost": label_response.get("shipment_cost", {}),
			"insurance_cost": label_response.get("insurance_cost", {}),
			"label_download": pdf_url,
			"trackable": label_response.get("trackable", True),
			"label_format": label_response.get("label_format"),
			"display_scheme": label_response.get("display_scheme"),
			"voided": label_response.get("voided", False),
			"voided_at": label_response.get("voided_at"),
		}
	return label_response


def _build_shipment_from_delivery_note(dn, carrier_id: str, service_code: str) -> dict:
	"""Build shipment payload from Delivery Note."""
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

	# Calculate total weight
	total_weight = 0.0
	for item in dn.items:
		item_weight = frappe.db.get_value("Item", item.item_code, "weight_per_unit") or 0
		total_weight += item_weight * item.qty

	if total_weight <= 0:
		total_weight = 1.0

	return {
		"carrier_id": carrier_id,
		"service_code": service_code,
		"ship_to": {
			"name": dn.customer_name or dn.customer,
			"address_line1": ship_to_address.address_line1,
			"address_line2": ship_to_address.address_line2 or "",
			"city_locality": ship_to_address.city,
			"state_province": ship_to_address.state,
			"postal_code": ship_to_address.pincode,
			"country_code": (frappe.db.get_value("Country", ship_to_address.country, "code") or "US").upper(),
			"phone": ship_to_address.phone or "",
		},
		"ship_from": {
			"name": dn.company,
			"address_line1": ship_from_address.address_line1,
			"address_line2": ship_from_address.address_line2 or "",
			"city_locality": ship_from_address.city,
			"state_province": ship_from_address.state,
			"postal_code": ship_from_address.pincode,
			"country_code": (frappe.db.get_value("Country", ship_from_address.country, "code") or "US").upper(),
			"phone": ship_from_address.phone or "",
		},
		"packages": [
			{
				"weight": {
					"value": total_weight,
					"unit": "pound",
				}
			}
		],
	}


def _download_and_attach_label(
	label_url: str,
	doctype: str,
	docname: str,
	settings: "ShipstationSettings",
) -> Optional["File"]:
	"""Download label PDF and attach to document."""
	import httpx

	try:
		api_key = settings.get_password("shipstation_api_key")
		headers = {"API-Key": api_key}

		with httpx.Client() as client:
			response = client.get(label_url, headers=headers)
			if response.status_code == 200:
				content = response.content
				# Check Content-Type header or PDF magic bytes to determine if already binary PDF
				content_type = response.headers.get("content-type", "")
				if "application/pdf" in content_type or content[:4] == b"%PDF":
					pdf_content = content
				else:
					# Assume base64 encoded
					try:
						pdf_content = base64.b64decode(content)
					except Exception:
						pdf_content = content

				pdf = BytesIO(pdf_content)

				# Ensure folder exists
				if not frappe.db.get_value("File", "Home/Shipstation Labels"):
					folder: "File" = frappe.new_doc("File")
					folder.update({"file_name": "Shipstation Labels", "is_folder": True, "folder": "Home"})
					folder.save()

				file_doc: "File" = save_file(
					fname=f"{docname}_shipstation_api.pdf",
					content=pdf.getvalue(),
					dt=doctype,
					dn=docname,
					folder="Home/Shipstation Labels",
					is_private=True,
				)
				return file_doc
	except Exception as e:
		frappe.log_error(title="Error downloading label PDF", message=str(e))

	return None

