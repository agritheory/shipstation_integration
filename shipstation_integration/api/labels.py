# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

"""
Direct label operations using ShipStation API v2.

This module provides functionality for purchasing, voiding, and
retrieving shipping labels directly through the API.
"""

import base64
import json
import re
from io import BytesIO
from typing import TYPE_CHECKING, Optional

import frappe
from frappe import _
from frappe.utils import cint, flt
from frappe.utils.file_manager import save_file
from shipengine.errors import ShipEngineError

from shipstation_integration.api.carriers import (
	get_carrier_capabilities,
	get_supplier_for_carrier_id,
)
from shipstation_integration.api.rates import (
	DIMENSION_UOM_MAP,
	WEIGHT_UOM_MAP,
	get_fallback_package,
	get_package_from_packing_slip,
	get_packages_from_packing_slip,
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
		return {}


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
		return {}


@frappe.whitelist()
def void_label_for_packing_slip(packing_slip: str, parcel_number: int) -> dict:
	"""Void the purchased label for one parcel and clear item tracking fields."""
	ps = frappe.get_doc("Packing Slip", packing_slip)
	parcel_number = cint(parcel_number)
	label_id = None
	for item in ps.items or []:
		if item.parcel_number == parcel_number and item.get("label_id"):
			label_id = item.label_id
			break

	if not label_id:
		frappe.throw(_("Parcel {0} has no label to void").format(parcel_number))

	result = void_label(label_id)
	if not result.get("approved"):
		return result

	clear_packing_slip_parcel_tracking(ps, parcel_number)
	remove_packing_slip_label_attachment(ps, parcel_number)
	return result


def clear_packing_slip_parcel_tracking(ps, parcel_number: int) -> None:
	cleared = {
		"tracking_number": None,
		"tracking_url": None,
		"label_url": None,
		"label_id": None,
	}
	for item in ps.items or []:
		if item.parcel_number == parcel_number:
			frappe.db.set_value("Packing Slip Item", item.name, cleared)


def remove_packing_slip_label_attachment(ps, parcel_number: int) -> None:
	remaining = any(
		item.parcel_number != parcel_number and item.get("label_id") for item in (ps.items or [])
	)
	if remaining:
		return

	file_names = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Packing Slip",
			"attached_to_name": ps.name,
			"file_name": f"{ps.name}_shipstation_api.pdf",
		},
		pluck="name",
	)
	for file_name in file_names:
		frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)


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
		return {}


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
		return {}


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
		return []

	if not ps.dispatch_address_name:
		frappe.throw(_("Packing Slip must have a dispatch (ship from) address"))
		return []

	existing = get_existing_label_info(ps)
	if existing and not force:
		frappe.throw(
			_(
				"A label has already been purchased for this Packing Slip (Tracking: {0}). Pass force=True to re-purchase."
			).format(existing["tracking_number"]),
			frappe.DuplicateEntryError,
			title=_("Label Already Exists"),
		)
		return []

	parcel_numbers = sorted({item.parcel_number for item in ps.items if item.parcel_number})
	if not parcel_numbers:
		frappe.throw(_("No items have been assigned to a parcel. Pack items before purchasing labels."))
		return []

	settings = get_shipstation_settings()

	if len(parcel_numbers) > 1:
		multi_responses = buy_multi_parcel_labels(ps, rate_id, carrier_id, service_code, parcel_numbers)
		if multi_responses:
			for label_response in multi_responses:
				update_packing_slip_tracking(ps, label_response, label_response["parcel_number"])
			return finalize_packing_slip_label_responses(ps, multi_responses, settings)

	label_responses = []
	for parcel_number in parcel_numbers:
		if rate_id:
			label_response = create_label_from_rate(rate_id=rate_id)
		else:
			if not carrier_id or not service_code:
				frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))
				return []
			assert carrier_id is not None and service_code is not None
			shipment_data = build_shipment_from_packing_slip(ps, carrier_id, service_code, parcel_number)
			label_response = create_label(shipment_data=shipment_data)

		label_response["parcel_number"] = parcel_number
		update_packing_slip_tracking(ps, label_response, parcel_number)
		label_responses.append(label_response)

	return finalize_packing_slip_label_responses(ps, label_responses, settings)


def finalize_packing_slip_label_responses(ps, label_responses: list[dict], settings) -> list[dict]:
	for label_response in label_responses:
		if label_response.get("label_download"):
			file_doc = download_and_attach_label(
				label_response["label_download"],
				ps.doctype,
				ps.name,
				settings,
			)
			label_response["attached_file"] = file_doc.name if file_doc else None
	return label_responses


def carrier_supports_multi_package(carrier_id: str, box_count: int) -> bool:
	"""Whether this carrier account can take every box as one shipment."""
	capabilities = get_carrier_capabilities(carrier_id)
	if not capabilities or capabilities.get("has_multi_package_supporting_services"):
		return True

	frappe.msgprint(
		_(
			"{0} has no multi-package service, so these {1} boxes ship as {1} separate "
			"labels and will not be numbered 1 of {1}. Choose another carrier if the "
			"boxes need numbering."
		).format(get_supplier_for_carrier_id(carrier_id) or _("This carrier"), box_count),
		indicator="orange",
		title=_("Boxes Will Not Be Numbered"),
	)
	return False


def split_package_labels(label_response: dict, parcel_numbers: list) -> list[dict]:
	"""One response per box out of a single multi-package purchase."""
	package_labels = label_response.get("packages") or []
	if len(package_labels) != len(parcel_numbers):
		return []

	responses = []
	for index, parcel_number in enumerate(parcel_numbers):
		package_label = package_labels[index]
		child = dict(label_response)
		child.pop("packages", None)
		child["parcel_number"] = parcel_number
		child["tracking_number"] = package_label.get("tracking_number") or label_response.get(
			"tracking_number"
		)
		child["label_id"] = package_label.get("label_id") or label_response.get("label_id")
		download = package_label.get("label_download")
		if isinstance(download, dict):
			download = download.get("pdf") or download.get("href")
		child["label_download"] = download or label_response.get("label_download")
		if index:
			child["shipment_cost"] = {}
			child["insurance_cost"] = {}
		responses.append(child)

	return responses


def labels_from_bought_rate(label_response: dict, parcel_numbers: list) -> list[dict]:
	"""Split a rate bought as one shipment into one response per box."""
	responses = split_package_labels(label_response, parcel_numbers)
	if responses:
		return responses

	frappe.msgprint(
		_(
			"The carrier returned a single label for a {0} box shipment, so the boxes are "
			"not numbered. Check the label before it goes on a carton."
		).format(len(parcel_numbers)),
		indicator="orange",
		title=_("Boxes Not Numbered"),
	)
	label_response["parcel_number"] = parcel_numbers[0]
	return [label_response]


def buy_multi_parcel_labels(
	ps, rate_id: str | None, carrier_id: str | None, service_code: str | None, parcel_numbers: list
) -> list[dict]:
	"""Buy every box on one shipment so the carrier numbers them 1 of N."""
	if rate_id:
		return labels_from_bought_rate(create_label_from_rate(rate_id=rate_id), parcel_numbers)

	if not carrier_id or not service_code:
		return []
	if not carrier_supports_multi_package(carrier_id, len(parcel_numbers)):
		return []

	packages = []
	for parcel_number in parcel_numbers:
		package = get_package_from_packing_slip(ps, parcel_number)
		if not package:
			return []
		packages.append(package)

	shipment_data = build_shipment_from_packing_slip(ps, carrier_id, service_code, parcel_numbers[0])
	shipment_data["packages"] = packages

	try:
		label_response = create_label(shipment_data=shipment_data)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Multi-package label failed for {ps.name}, falling back to one per box",
		)
		return []

	return split_package_labels(label_response, parcel_numbers)


def buy_multi_parcel_shipment_labels(
	doc,
	rate_id: str | None,
	carrier_id: str | None,
	service_code: str | None,
	parcel_numbers: list,
) -> list[dict]:
	"""Buy every box on a Shipment as one carrier shipment."""
	if rate_id:
		return labels_from_bought_rate(create_label_from_rate(rate_id=rate_id), parcel_numbers)

	if not carrier_id or not service_code:
		return []
	if not carrier_supports_multi_package(carrier_id, len(parcel_numbers)):
		return []

	shipment_data = build_shipment_from_shipment_doc(doc, carrier_id, service_code, parcel_numbers[0])
	packages = []
	for parcel_number in parcel_numbers:
		per_parcel = build_shipment_from_shipment_doc(doc, carrier_id, service_code, parcel_number)
		packages.extend(per_parcel.get("packages") or [])
	if not packages:
		return []
	shipment_data["packages"] = packages

	try:
		label_response = create_label(shipment_data=shipment_data)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			f"Multi-package label failed for {doc.name}, falling back to one per box",
		)
		return []

	return split_package_labels(label_response, parcel_numbers)


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
		return {}

	if not dn.dispatch_address_name:
		frappe.throw(_("Delivery Note must have a dispatch (ship from) address"))
		return {}

	settings = get_shipstation_settings()

	if rate_id:
		label_response = create_label_from_rate(rate_id=rate_id)
	else:
		if not carrier_id or not service_code:
			frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))
			return {}
		assert carrier_id is not None and service_code is not None
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


def label_billing_context(delivery_note: str | None, carrier_id: str, rows=None):
	"""Minimal packing-slip-shaped context for billing helpers."""
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_first_sales_order_from_pack_lines,
	)

	supplier = get_supplier_for_carrier_id(carrier_id)
	ctx = frappe._dict({"carrier": supplier, "items": rows or []})
	if delivery_note:
		ctx.delivery_note = delivery_note
	so_name = get_first_sales_order_from_pack_lines(rows or [])
	if so_name and not ctx.delivery_note:
		ctx.items = [{"against_sales_order": so_name}]
	return ctx, so_name


def resolve_label_billing_for_source(
	delivery_note: str | None, carrier_id: str, rows=None
) -> dict | None:
	ctx, _so_name = label_billing_context(delivery_note, carrier_id, rows)
	if not ctx.carrier:
		return None
	return resolve_label_billing_options(ctx)


def resolve_label_reference_for_source(rows, delivery_note: str | None = None) -> str:
	return resolve_label_reference(label_reference_context(rows, delivery_note))


def label_reference_context(rows, delivery_note: str | None = None):
	ctx = frappe._dict(items=rows or [])
	if delivery_note:
		ctx.delivery_note = delivery_note
	return ctx


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

	shipment = {
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

	validate_po_box_delivery(ship_to_address, service_code, get_supplier_for_carrier_id(carrier_id))

	reference = resolve_label_reference_for_source(dn.items, dn.name)
	if reference:
		shipment["external_order_id"] = reference

	billing = resolve_label_billing_for_source(dn.name, carrier_id, dn.items)
	if billing:
		shipment["advanced_options"] = billing

	return shipment


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
		"label_id": label_response.get("label_id"),
	}

	for item in ps.items:
		if item.parcel_number == parcel_number:
			frappe.db.set_value("Packing Slip Item", item.name, tracking_data)


def build_tracking_url(tracking_number: str | None, carrier_code: str) -> str:
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
		return {}


def format_label_download(label_download):
	if isinstance(label_download, dict):
		return label_download.get("pdf") or label_download.get("href")
	return label_download


def format_label_response(label_response) -> dict:
	"""Format label response for frontend consumption."""
	if isinstance(label_response, dict):
		formatted = {
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
			"label_download": format_label_download(label_response.get("label_download", {})),
			"trackable": label_response.get("trackable", True),
			"label_format": label_response.get("label_format"),
			"display_scheme": label_response.get("display_scheme"),
			"voided": label_response.get("voided", False),
			"voided_at": label_response.get("voided_at"),
		}
		packages = label_response.get("packages") or []
		if packages:
			formatted["packages"] = [
				{
					"label_id": package.get("label_id"),
					"tracking_number": package.get("tracking_number"),
					"label_download": format_label_download(package.get("label_download", {})),
				}
				for package in packages
			]
		return formatted
	return label_response


PO_BOX_CARRIERS = ("usps", "stamps_com", "stamps.com", "globalpost", "dhl_ecommerce")
PO_BOX_PATTERN = re.compile(
	r"\b(?:p[\s.]*o[\s.]*box|post\s+office\s+box|postal\s+box)\b", re.IGNORECASE
)


def is_po_box(address) -> bool:
	"""True when either address line names a PO box rather than a street."""
	return any(
		PO_BOX_PATTERN.search(address.get(fieldname) or "")
		for fieldname in ("address_line1", "address_line2")
	)


def validate_po_box_delivery(ship_to_address, service_code, carrier=None):
	"""Block label purchase when a non-postal carrier cannot deliver to a PO box."""
	if not is_po_box(ship_to_address):
		return

	selected = f"{service_code or ''} {carrier or ''}".lower()
	if any(postal in selected for postal in PO_BOX_CARRIERS):
		return

	frappe.throw(
		_(
			"{0} is a PO box and {1} does not deliver to PO boxes. Use a street address "
			"or ship this parcel by USPS."
		).format(ship_to_address.address_line1, carrier or service_code or _("this carrier")),
		title=_("PO Box Not Deliverable"),
	)


def decline_customer_billing(source, incoterm, reason: str) -> None:
	"""Explain when customer freight billing was expected but cannot be applied."""
	from shipstation_integration.incoterms import normalize_incoterm_code
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_customer_from_packing_slip,
	)

	source_name = getattr(source, "name", None) or source.get("name") or _("this shipment")
	incoterm_label = normalize_incoterm_code(incoterm) or incoterm or _("customer-carriage")
	problem = _("{0} is sold under Incoterm {1}, but {2}.").format(
		source_name, incoterm_label, reason
	)

	bill_to_party = (source.get("bill_to_party") or "").strip()
	if bill_to_party and bill_to_party != "Shipper":
		frappe.throw(problem, title=_("Billing Account Not Found"))

	customer, _customer_display = get_customer_from_packing_slip(source)
	shipper = source.get("company")
	if not shipper and source.get("delivery_note"):
		shipper = frappe.db.get_value("Delivery Note", source.delivery_note, "company")
	if not shipper and customer:
		shipper = frappe.db.get_value(
			"Customer", customer, "default_company"
		) or frappe.defaults.get_global_default("company")

	frappe.msgprint(
		problem + " " + _("This label bills {0} instead.").format(shipper or _("the shipper account")),
		indicator="orange",
		title=_("Freight Billed to Shipper"),
	)


def resolve_label_billing_options(packing_slip) -> dict | None:
	"""Return ShipEngine advanced_options, or None for shipper (Prepaid) billing."""
	hooks = frappe.get_hooks("get_label_billing_options") or []
	if not hooks:
		return default_label_billing_options(packing_slip)
	return frappe.get_attr(hooks[-1])(packing_slip)


def resolve_label_reference(packing_slip) -> str:
	"""Return the shipment reference string for the label payload."""
	hooks = frappe.get_hooks("get_label_reference") or []
	if not hooks:
		return default_label_reference(packing_slip)
	return frappe.get_attr(hooks[-1])(packing_slip) or ""


def default_label_billing_options(packing_slip) -> dict | None:
	"""Bill the customer's carrier account when the order incoterm requires it."""
	from shipstation_integration.incoterms import (
		incoterm_requires_customer_shipping_account,
		resolve_incoterm_for_source,
	)

	incoterm = resolve_incoterm_for_source(packing_slip)
	if not incoterm_requires_customer_shipping_account(incoterm):
		return None
	return billing_options_for_incoterm(packing_slip, incoterm)


def default_label_reference(packing_slip) -> str:
	"""Use the linked Sales Order name, or empty when none is linked."""
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_first_sales_order_from_packing_slip,
	)

	return get_first_sales_order_from_packing_slip(packing_slip) or ""


def billing_options_for_incoterm(packing_slip, incoterm) -> dict | None:
	"""Map customer-carriage incoterms to ShipEngine advanced_options."""
	from shipstation_integration.incoterms import incoterm_requires_customer_shipping_account
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_customer_from_packing_slip,
	)

	if not incoterm_requires_customer_shipping_account(incoterm):
		return None

	if not packing_slip.get("carrier"):
		decline_customer_billing(packing_slip, incoterm, _("no carrier is set on the shipment"))
		return None

	customer, _customer_display = get_customer_from_packing_slip(packing_slip)
	if not customer:
		decline_customer_billing(packing_slip, incoterm, _("no customer is linked"))
		return None

	options = shipping_account_billing_options(packing_slip)
	if options is None:
		decline_customer_billing(
			packing_slip,
			incoterm,
			_("customer {0} has no {1} account on file").format(customer, packing_slip.carrier),
		)
	return options


def shipping_account_billing_options(packing_slip) -> dict | None:
	"""Build third-party advanced_options from the customer's mapped account."""
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_customer_from_packing_slip,
	)

	if not packing_slip.carrier:
		return None

	customer, _customer_display = get_customer_from_packing_slip(packing_slip)
	if not customer:
		return None

	customer_doc = frappe.get_cached_doc("Customer", customer)
	accounts = [
		row for row in (customer_doc.shipping_accounts or []) if row.carrier == packing_slip.carrier
	]
	if not accounts:
		return None

	account = (
		next((a for a in accounts if a.default), None)
		or next((a for a in accounts if a.enabled), None)
		or accounts[0]
	)
	if not account.shipping_account_number:
		return None

	billing_address = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Customer", "link_name": customer, "parenttype": "Address"},
		"parent",
	)
	postal_code = ""
	country_code = "US"
	if billing_address:
		addr = frappe.get_cached_doc("Address", billing_address)
		postal_code = addr.pincode or ""
		country_code = (frappe.db.get_value("Country", addr.country, "code") or "US").upper()

	options: dict = {
		"bill_to_party": "third_party",
		"bill_to_account": account.shipping_account_number,
		"bill_to_country_code": country_code,
	}
	if postal_code:
		options["bill_to_postal_code"] = postal_code
	return options


def get_third_party_billing_options(ps) -> dict | None:
	"""Return ShipEngine advanced_options from the label billing seam."""
	return resolve_label_billing_options(ps)


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

	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_company_from_packing_slip,
		get_customer_from_packing_slip,
	)

	company_name = get_company_from_packing_slip(ps)
	customer, customer_name = get_customer_from_packing_slip(ps)
	if not company_name or not customer:
		frappe.throw(_("Packing Slip must be linked to a Delivery Note or Sales Order"))
	customer_name = customer_name or customer

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

	validate_po_box_delivery(ship_to_address, service_code, ps.get("carrier"))

	shipment: dict = {
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

	reference = resolve_label_reference(ps)
	if reference:
		shipment["external_order_id"] = reference

	billing = resolve_label_billing_options(ps)
	if billing:
		shipment["advanced_options"] = billing

	return shipment


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
		return []
	if not doc.delivery_address_name:
		frappe.throw(_("Shipment must have a delivery (ship to) address"))
		return []

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
		return []

	parcel_numbers = sorted(
		{row.parcel_number for row in (doc.shipment_delivery_note or []) if row.parcel_number}
	)
	if not parcel_numbers:
		frappe.throw(
			_("No SDN items have been assigned to a parcel. Pack items before purchasing labels.")
		)
		return []

	settings = get_shipstation_settings()

	if len(parcel_numbers) > 1:
		multi_responses = buy_multi_parcel_shipment_labels(
			doc, rate_id, carrier_id, service_code, parcel_numbers
		)
		if multi_responses:
			for label_response in multi_responses:
				if label_response.get("label_download"):
					file_doc = download_and_attach_label(
						label_response["label_download"],
						doc.doctype,
						doc.name,
						settings,
					)
					label_response["attached_file"] = file_doc.name if file_doc else None
				update_shipment_delivery_note_tracking(doc, label_response, label_response["parcel_number"])
			return multi_responses

	label_responses = []
	for parcel_number in parcel_numbers:
		if rate_id:
			label_response = create_label_from_rate(rate_id=rate_id)
		else:
			if not carrier_id or not service_code:
				frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))
				return []
			assert carrier_id is not None and service_code is not None
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
	from shipstation_integration.api.rates import get_state_code

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

	if not parcel_rows:
		frappe.throw(
			_("Parcel {0} has no items with parcel dimensions configured").format(parcel_number)
		)

	ref = parcel_rows[0]
	weight = flt(ref.parcel_weight)
	length = flt(ref.parcel_length)
	width = flt(ref.parcel_width)
	height = flt(ref.parcel_height)
	if not weight:
		frappe.throw(_("Parcel {0} is missing weight").format(parcel_number))
	if not length or not width or not height:
		frappe.throw(_("Parcel {0} is missing dimensions").format(parcel_number))

	dimension_unit = DIMENSION_UOM_MAP.get(ref.dimension_uom, "inch")
	weight_unit = WEIGHT_UOM_MAP.get(ref.parcel_weight_uom, "pound")
	package = {
		"weight": {
			"value": weight,
			"unit": weight_unit,
		},
		"dimensions": {
			"length": length,
			"width": width,
			"height": height,
			"unit": dimension_unit,
		},
	}

	if carrier_id and not str(carrier_id).startswith("se-"):
		frappe.throw(
			_(
				"Invalid carrier ID format: {0}. Expected a ShipEngine carrier ID (e.g. se-123456). "
				"Please sync carriers in Shipstation Settings."
			).format(carrier_id)
		)
		return {}

	delivery_note = next(
		(row.delivery_note for row in (doc.shipment_delivery_note or []) if row.get("delivery_note")),
		None,
	)
	shipment = {
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

	validate_po_box_delivery(
		ship_to_address,
		service_code,
		get_supplier_for_carrier_id(carrier_id),
	)

	reference = resolve_label_reference_for_source(doc.shipment_delivery_note, delivery_note)
	if reference:
		shipment["external_order_id"] = reference

	billing = resolve_label_billing_for_source(delivery_note, carrier_id, doc.shipment_delivery_note)
	if billing:
		shipment["advanced_options"] = billing

	return shipment


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
