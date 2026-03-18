# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

"""
Direct label operations using ShipStation API v2.

This module provides functionality for purchasing, voiding, and
retrieving shipping labels directly through the API.
"""

import base64
import json
from io import BytesIO
from typing import TYPE_CHECKING, Optional

import frappe
from frappe import _
from frappe.utils import flt
from frappe.utils.file_manager import save_file
from shipengine.errors import ShipEngineError

from shipstation_integration.rates import (
	DIMENSION_UOM_MAP,
	WEIGHT_UOM_MAP,
	get_fallback_package,
	get_package_from_packing_slip,
	get_state_code,
)
from shipstation_integration.utils import get_error_message, get_shipstation_settings

if TYPE_CHECKING:
	from frappe.core.doctype.file.file import File

	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


@frappe.whitelist()
def create_label(
	shipment_data: dict,
	settings_name: str | None = None,
) -> dict:
	"""
	Purchase a shipping label directly.

	Args:
	        shipment_data: Shipment dict with ship_to, ship_from, packages, carrier_id, service_code
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Label response with label_id, tracking_number, label_download URL
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	# ShipEngine SDK expects data wrapped in a "shipment" key
	wrapped_data = {"shipment": shipment_data}

	try:
		label_response = client.create_label_from_shipment(wrapped_data)
		return format_label_response(label_response)
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(
			title="Error creating shipping label",
			message=f"Error: {error_msg}\n\nShipment data: {json.dumps(shipment_data, indent=2, default=str)}",
		)
		frappe.throw(_("Failed to create shipping label: {0}").format(error_msg))


@frappe.whitelist()
def create_label_from_rate(
	rate_id: str,
	settings_name: str | None = None,
) -> dict:
	"""
	Purchase a shipping label using a pre-selected rate.

	Args:
	        rate_id: Rate ID from a previous rate request
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Label response with label_id, tracking_number, label_download URL
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		# create_label_from_rate_id requires rate_id and params dict
		label_params = {
			"label_format": "pdf",
			"label_layout": "4x6",
		}
		label_response = client.create_label_from_rate_id(rate_id=rate_id, params=label_params)
		return format_label_response(label_response)
	except Exception as e:
		error_msg = get_error_message(e)
		frappe.log_error(title="Error creating label from rate", message=error_msg)
		frappe.throw(_("Failed to create label from rate: {0}").format(error_msg))


@frappe.whitelist()
def void_label(
	label_id: str,
	settings_name: str | None = None,
) -> dict:
	"""
	Void a previously purchased label.

	Args:
	        label_id: The label ID to void
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Void response with status
	"""
	settings = get_shipstation_settings(settings_name)
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
	settings_name: str | None = None,
) -> dict:
	"""
	Retrieve label details by ID.

	Args:
	        label_id: The label ID to retrieve
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Label details dict
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		label = client.get_label_by_id(label_id=label_id)
		return format_label_response(label)
	except Exception as e:
		frappe.log_error(title="Error fetching label", message=str(e))
		frappe.throw(_("Failed to fetch label: {0}").format(str(e)))


@frappe.whitelist()
def create_label_for_packing_slip(
	packing_slip: str,
	rate_id: str | None = None,
	carrier_id: str | None = None,
	service_code: str | None = None,
	force: bool = False,
) -> list[dict]:
	"""
	Create shipping labels for a Packing Slip using ShipStation API v2.

	One label is created per unique parcel_number found in the Packing Slip
	Item rows. Each label gets its own tracking number which is written back
	only to the items belonging to that parcel.

	Args:
	        packing_slip: Packing Slip document name
	        rate_id: Optional rate ID from previous rate request (single-parcel only)
	        carrier_id: Carrier ID (required if rate_id not provided)
	        service_code: Service code (required if rate_id not provided)
	        force: If True, allow re-purchasing even if tracking numbers already exist

	Returns:
	        List of label responses (one per parcel)
	"""
	ps = frappe.get_doc("Packing Slip", packing_slip)

	if not ps.shipping_address_name:
		frappe.throw(_("Packing Slip must have a shipping address"))

	if not ps.dispatch_address_name:
		frappe.throw(_("Packing Slip must have a dispatch (ship from) address"))

	existing = get_existing_label_info(ps)
	if existing and not force:
		frappe.throw(
			_(
				"A label has already been purchased for this Packing Slip (Tracking: {0}). Pass force=True to re-purchase."
			).format(existing["tracking_number"]),
			frappe.DuplicateEntryError,
			title=_("Label Already Exists"),
		)

	parcel_numbers = sorted({item.parcel_number for item in ps.items if item.parcel_number})
	if not parcel_numbers:
		frappe.throw(_("No items have been assigned to a parcel. Pack items before purchasing labels."))

	if rate_id and len(parcel_numbers) > 1:
		frappe.throw(
			_(
				"rate_id can only be used for single-parcel shipments. Use carrier_id and service_code for multi-parcel."
			)
		)

	settings = get_shipstation_settings()
	label_responses = []

	for parcel_number in parcel_numbers:
		if rate_id:
			label_response = create_label_from_rate(rate_id=rate_id)
		else:
			if not carrier_id or not service_code:
				frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))

			shipment_data = build_shipment_from_packing_slip(ps, carrier_id, service_code, parcel_number)
			label_response = create_label(shipment_data=shipment_data)

		if label_response.get("label_download"):
			file_doc = download_and_attach_label(
				label_response["label_download"],
				ps.doctype,
				ps.name,
				settings,
			)
			label_response["attached_file"] = file_doc.name if file_doc else None

		label_response["parcel_number"] = parcel_number
		update_packing_slip_tracking(ps, label_response, parcel_number)
		label_responses.append(label_response)

	return label_responses


def get_existing_label_info(ps) -> dict | None:
	"""Return tracking info from the first Packing Slip Item row that has a tracking number, or None."""
	for row in ps.items or []:
		if row.get("tracking_number"):
			return {
				"tracking_number": row.tracking_number,
				"tracking_url": row.tracking_url or "",
				"label_url": row.label_url or "",
				"carrier": row.carrier or "",
			}
	return None


@frappe.whitelist()
def create_label_for_delivery_note(
	delivery_note: str,
	rate_id: str | None = None,
	carrier_id: str | None = None,
	service_code: str | None = None,
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

	if not dn.dispatch_address_name:
		frappe.throw(_("Delivery Note must have a dispatch (ship from) address"))

	settings = get_shipstation_settings()

	if rate_id:
		label_response = create_label_from_rate(rate_id=rate_id)
	else:
		if not carrier_id or not service_code:
			frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))

		shipment_data = build_shipment_from_delivery_note(dn, carrier_id, service_code)
		label_response = create_label(shipment_data=shipment_data)

	if label_response.get("label_download"):
		file_doc = download_and_attach_label(
			label_response["label_download"],
			dn.doctype,
			dn.name,
			settings,
		)
		label_response["attached_file"] = file_doc.name if file_doc else None

	update_delivery_note_tracking(dn, label_response)

	return label_response


def build_shipment_from_delivery_note(dn, carrier_id: str, service_code: str) -> dict:
	"""Build shipment payload from Delivery Note."""
	ship_to_address = frappe.get_doc("Address", dn.shipping_address_name)
	ship_from_address = frappe.get_doc("Address", dn.dispatch_address_name)

	ship_to_country = (
		frappe.db.get_value("Country", ship_to_address.country, "code") or "US"
	).upper()
	ship_from_country = (
		frappe.db.get_value("Country", ship_from_address.country, "code") or "US"
	).upper()

	ship_to_phone = ship_to_address.phone or "0000000000"
	ship_from_phone = ship_from_address.phone or "0000000000"

	package = get_fallback_package(dn)

	return {
		"carrier_id": carrier_id,
		"service_code": service_code,
		"ship_to": {
			"name": dn.customer_name or dn.customer,
			"phone": ship_to_phone,
			"address_line1": ship_to_address.address_line1,
			"address_line2": ship_to_address.address_line2 or "",
			"city_locality": ship_to_address.city,
			"state_province": get_state_code(ship_to_address.state, ship_to_country),
			"postal_code": ship_to_address.pincode,
			"country_code": ship_to_country,
		},
		"ship_from": {
			"name": dn.company,
			"phone": ship_from_phone,
			"address_line1": ship_from_address.address_line1,
			"address_line2": ship_from_address.address_line2 or "",
			"city_locality": ship_from_address.city,
			"state_province": get_state_code(ship_from_address.state, ship_from_country),
			"postal_code": ship_from_address.pincode,
			"country_code": ship_from_country,
		},
		"packages": [package],
	}


def update_delivery_note_tracking(dn, label_response: dict) -> None:
	"""Update the Delivery Note with label and tracking info."""
	tracking_number = label_response.get("tracking_number")
	carrier_code = label_response.get("carrier_code", "").upper()
	tracking_url = build_tracking_url(tracking_number, carrier_code)

	frappe.db.set_value(
		dn.doctype,
		dn.name,
		{
			"tracking_number": tracking_number,
			"carrier": carrier_code,
		},
	)


def update_packing_slip_tracking(ps, label_response: dict, parcel_number: int) -> None:
	"""
	Update Packing Slip Item rows belonging to ``parcel_number`` with label/tracking info.

	Each parcel gets its own tracking number, so only items assigned to the
	given parcel are updated.
	"""
	tracking_number = label_response.get("tracking_number")
	label_url = label_response.get("label_download")
	carrier_code = label_response.get("carrier_code", "").upper()
	tracking_url = build_tracking_url(tracking_number, carrier_code)

	tracking_data = {
		"tracking_number": tracking_number,
		"tracking_url": tracking_url,
		"label_url": label_url,
	}

	for item in ps.items:
		if item.parcel_number == parcel_number:
			frappe.db.set_value("Packing Slip Item", item.name, tracking_data)


def build_tracking_url(tracking_number: str, carrier_code: str) -> str:
	"""Build carrier-specific tracking URL."""
	if not tracking_number:
		return ""

	carrier_code = (carrier_code or "").lower()

	tracking_urls = {
		"ups": f"https://www.ups.com/track?tracknum={tracking_number}",
		"usps": f"https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking_number}",
		"fedex": f"https://www.fedex.com/fedextrack/?trknbr={tracking_number}",
		"dhl": f"https://www.dhl.com/en/express/tracking.html?AWB={tracking_number}",
	}

	return tracking_urls.get(carrier_code, "")


@frappe.whitelist()
def create_return_label(
	label_id: str,
	settings_name: str | None = None,
) -> dict:
	"""
	Create a return label for a previously created outbound label.

	Args:
	        label_id: The original outbound label ID
	        settings_name: Optional Shipstation Settings document name

	Returns:
	        Return label response
	"""
	settings = get_shipstation_settings(settings_name)
	client = settings.shipstation_api_client()

	try:
		return_response = client.create_return_label(label_id=label_id)
		return format_label_response(return_response)
	except Exception as e:
		frappe.log_error(title="Error creating return label", message=str(e))
		frappe.throw(_("Failed to create return label: {0}").format(str(e)))


def format_label_response(label_response) -> dict:
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


def build_shipment_from_packing_slip(
	ps, carrier_id: str, service_code: str, parcel_number: int
) -> dict:
	"""
	Build shipment payload from Packing Slip for a specific parcel.

	Addresses come from the Packing Slip's address fields. Package dimensions
	are sourced from items assigned to ``parcel_number``.
	"""
	ship_to_address = frappe.get_doc("Address", ps.shipping_address_name)
	ship_from_address = frappe.get_doc("Address", ps.dispatch_address_name)

	dn = frappe.get_doc("Delivery Note", ps.delivery_note)
	company_name = dn.company
	customer_name = dn.customer_name or dn.customer

	if carrier_id and not str(carrier_id).startswith("se-"):
		frappe.throw(
			_(
				"Invalid carrier ID format: {0}. Expected a ShipEngine carrier ID (e.g. se-123456). "
				"Please sync carriers in Shipstation Settings."
			).format(carrier_id)
		)

	package = get_package_from_packing_slip(ps, parcel_number)
	if not package:
		frappe.throw(
			_("Parcel {0} has no items with parcel dimensions configured").format(parcel_number)
		)

	# Get country codes and convert state names to 2-char codes for US
	ship_to_country = (
		frappe.db.get_value("Country", ship_to_address.country, "code") or "US"
	).upper()
	ship_from_country = (
		frappe.db.get_value("Country", ship_from_address.country, "code") or "US"
	).upper()

	ship_to_phone = ship_to_address.phone or "0000000000"
	ship_from_phone = ship_from_address.phone or "0000000000"

	return {
		"carrier_id": carrier_id,
		"service_code": service_code,
		"ship_to": {
			"name": customer_name,
			"phone": ship_to_phone,
			"address_line1": ship_to_address.address_line1,
			"address_line2": ship_to_address.address_line2 or "",
			"city_locality": ship_to_address.city,
			"state_province": get_state_code(ship_to_address.state, ship_to_country),
			"postal_code": ship_to_address.pincode,
			"country_code": ship_to_country,
		},
		"ship_from": {
			"name": company_name,
			"phone": ship_from_phone,
			"address_line1": ship_from_address.address_line1,
			"address_line2": ship_from_address.address_line2 or "",
			"city_locality": ship_from_address.city,
			"state_province": get_state_code(ship_from_address.state, ship_from_country),
			"postal_code": ship_from_address.pincode,
			"country_code": ship_from_country,
		},
		"packages": [package],
	}


@frappe.whitelist()
def create_label_for_shipment(
	shipment: str,
	rate_id: str | None = None,
	carrier_id: str | None = None,
	service_code: str | None = None,
	force: bool = False,
) -> list[dict]:
	"""
	Create shipping labels for a Shipment using ShipStation API v2.

	One label is created per unique parcel_number found in the Shipment
	Delivery Note rows.  Each label gets its own tracking number which is
	written back to the SDN rows belonging to that parcel.

	Args:
	        shipment: Shipment document name
	        rate_id: Optional rate ID from previous rate request (single-parcel only)
	        carrier_id: Carrier ID (required if rate_id not provided)
	        service_code: Service code (required if rate_id not provided)
	        force: If True, allow re-purchasing even if tracking numbers already exist

	Returns:
	        List of label responses (one per parcel)
	"""
	doc = frappe.get_doc("Shipment", shipment)

	if not doc.pickup_address_name:
		frappe.throw(_("Shipment must have a pickup (ship from) address"))
	if not doc.delivery_address_name:
		frappe.throw(_("Shipment must have a delivery (ship to) address"))

	# Check for existing labels
	existing_tracking = next(
		(row.tracking_number for row in (doc.shipment_delivery_note or []) if row.tracking_number),
		None,
	)
	if existing_tracking and not force:
		frappe.throw(
			_(
				"A label has already been purchased for this Shipment (Tracking: {0}). "
				"Pass force=True to re-purchase."
			).format(existing_tracking),
			frappe.DuplicateEntryError,
			title=_("Label Already Exists"),
		)

	parcel_numbers = sorted(
		{row.parcel_number for row in (doc.shipment_delivery_note or []) if row.parcel_number}
	)
	if not parcel_numbers:
		frappe.throw(
			_("No SDN items have been assigned to a parcel. Pack items before purchasing labels.")
		)

	if rate_id and len(parcel_numbers) > 1:
		frappe.throw(
			_(
				"rate_id can only be used for single-parcel shipments. "
				"Use carrier_id and service_code for multi-parcel."
			)
		)

	settings = get_shipstation_settings()
	label_responses = []

	for parcel_number in parcel_numbers:
		if rate_id:
			label_response = create_label_from_rate(rate_id=rate_id)
		else:
			if not carrier_id or not service_code:
				frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))

			shipment_data = build_shipment_from_shipment_doc(doc, carrier_id, service_code, parcel_number)
			label_response = create_label(shipment_data=shipment_data)

		if label_response.get("label_download"):
			file_doc = download_and_attach_label(
				label_response["label_download"],
				doc.doctype,
				doc.name,
				settings,
			)
			label_response["attached_file"] = file_doc.name if file_doc else None

		label_response["parcel_number"] = parcel_number
		update_shipment_delivery_note_tracking(doc, label_response, parcel_number)
		label_responses.append(label_response)

	return label_responses


def build_shipment_from_shipment_doc(
	doc, carrier_id: str, service_code: str, parcel_number: int
) -> dict:
	"""
	Build ShipEngine shipment payload from a Shipment document for a specific parcel.

	Addresses come from the Shipment's pickup_address_name (ship from) and
	delivery_address_name (ship to).  Package dimensions are sourced from SDN
	rows assigned to parcel_number.
	"""
	from shipstation_integration.rates import get_state_code

	ship_from_address = frappe.get_doc("Address", doc.pickup_address_name)
	ship_to_address = frappe.get_doc("Address", doc.delivery_address_name)

	ship_from_country = (
		frappe.db.get_value("Country", ship_from_address.country, "code") or "US"
	).upper()
	ship_to_country = (
		frappe.db.get_value("Country", ship_to_address.country, "code") or "US"
	).upper()

	# Derive a customer/recipient name from the delivery address
	recipient_name = ship_to_address.address_title or doc.delivery_to

	parcel_rows = [
		row for row in (doc.shipment_delivery_note or []) if row.parcel_number == parcel_number
	]

	if parcel_rows:
		ref = parcel_rows[0]
		dimension_unit = DIMENSION_UOM_MAP.get(ref.dimension_uom, "inch")
		weight_unit = WEIGHT_UOM_MAP.get(ref.parcel_weight_uom, "pound")
		package = {
			"weight": {
				"value": flt(ref.parcel_weight) or 1.0,
				"unit": weight_unit,
			},
			"dimensions": {
				"length": flt(ref.parcel_length) or 1,
				"width": flt(ref.parcel_width) or 1,
				"height": flt(ref.parcel_height) or 1,
				"unit": dimension_unit,
			},
		}
	else:
		package = {
			"weight": {"value": 1.0, "unit": "pound"},
			"dimensions": {"length": 12, "width": 9, "height": 6, "unit": "inch"},
		}

	if carrier_id and not str(carrier_id).startswith("se-"):
		frappe.throw(
			_(
				"Invalid carrier ID format: {0}. Expected a ShipEngine carrier ID (e.g. se-123456). "
				"Please sync carriers in Shipstation Settings."
			).format(carrier_id)
		)

	return {
		"carrier_id": carrier_id,
		"service_code": service_code,
		"ship_to": {
			"name": recipient_name,
			"phone": ship_to_address.phone or "0000000000",
			"address_line1": ship_to_address.address_line1,
			"address_line2": ship_to_address.address_line2 or "",
			"city_locality": ship_to_address.city,
			"state_province": get_state_code(ship_to_address.state, ship_to_country),
			"postal_code": ship_to_address.pincode,
			"country_code": ship_to_country,
		},
		"ship_from": {
			"name": doc.company,
			"phone": ship_from_address.phone or "0000000000",
			"address_line1": ship_from_address.address_line1,
			"address_line2": ship_from_address.address_line2 or "",
			"city_locality": ship_from_address.city,
			"state_province": get_state_code(ship_from_address.state, ship_from_country),
			"postal_code": ship_from_address.pincode,
			"country_code": ship_from_country,
		},
		"packages": [package],
	}


def update_shipment_delivery_note_tracking(doc, label_response: dict, parcel_number: int) -> None:
	"""
	Update Shipment Delivery Note rows belonging to parcel_number with
	label/tracking info.
	"""
	tracking_number = label_response.get("tracking_number")
	label_url = label_response.get("label_download")
	carrier_code = label_response.get("carrier_code", "").upper()
	tracking_url = build_tracking_url(tracking_number, carrier_code)

	tracking_data = {
		"tracking_number": tracking_number,
		"tracking_url": tracking_url,
		"label_url": label_url,
	}

	for row in doc.shipment_delivery_note or []:
		if row.parcel_number == parcel_number:
			frappe.db.set_value("Shipment Delivery Note", row.name, tracking_data)


def download_and_attach_label(
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
