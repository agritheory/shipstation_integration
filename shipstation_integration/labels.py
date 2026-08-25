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
from frappe.utils import flt
from frappe.utils.file_manager import save_file
from shipengine.errors import ShipEngineError

from shipstation_integration.carriers import (
	get_carrier_capabilities,
	get_supplier_for_carrier_id,
)
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

		# If the carrier rejected third-party billing, retry without it and warn.
		# This allows label creation to proceed; the user can collect the shipping
		# cost from the customer through other means.
		if (
			"advanced_options" in shipment_data
			and "third party" in error_msg.lower()
			and "bill to party" in error_msg.lower()
		):
			fallback_data = {k: v for k, v in shipment_data.items() if k != "advanced_options"}
			try:
				label_response = client.create_label_from_shipment({"shipment": fallback_data})
				frappe.msgprint(
					_(
						"Third-party billing was rejected by the carrier. "
						"The label was created and billed to your account instead. "
						"You may need to collect the shipping cost from the customer separately."
					),
					title=_("Third-Party Billing Unavailable"),
					indicator="orange",
				)
				return format_label_response(label_response)
			except Exception as e2:
				error_msg = get_error_message(e2)

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
		# The ShipEngine SDK has no `void_label` method; the voiding endpoint is
		# exposed as void_label_with_label_id (older SDKs: void_label_by_label_id).
		voider = getattr(client, "void_label_with_label_id", None) or getattr(
			client, "void_label_by_label_id", None
		)
		if not voider:
			frappe.throw(_("Installed ShipEngine SDK does not support voiding labels"))
		try:
			void_response = voider(label_id)
		except TypeError:
			void_response = voider(label_id, None)
		if not isinstance(void_response, dict):
			void_response = getattr(void_response, "__dict__", {}) or {}
		return {
			"approved": void_response.get("approved", False),
			"message": void_response.get("message", ""),
		}
	except Exception as e:
		frappe.log_error(title="Error voiding label", message=str(e))
		frappe.throw(_("Failed to void label: {0}").format(str(e)))
		return {}


@frappe.whitelist()
def void_labels_for_packing_slip(packing_slip: str) -> list[dict]:
	"""Void every purchased label on a Packing Slip and clear its tracking fields."""
	ps = frappe.get_doc("Packing Slip", packing_slip)

	rows = [row for row in (ps.items or []) if row.get("tracking_number")]
	if not rows:
		frappe.throw(_("No purchased labels found on this Packing Slip"))

	# Checked before anything is cleared. A throw part way through rolls the tracking
	# clear back with it, which is why this used to look like the button did nothing.
	if not any(row.get("label_id") for row in rows):
		frappe.throw(
			_(
				"No label IDs are stored on this Packing Slip, so these labels cannot be voided "
				"through ShipStation (they were bought before voiding was supported). Void them "
				"in the carrier portal, then use Clear Tracking to free this Packing Slip for a "
				"new label."
			),
			title=_("Cannot Void"),
		)

	voided = []
	seen_labels = set()
	for row in rows:
		label_id = row.get("label_id")
		if label_id and label_id not in seen_labels:
			seen_labels.add(label_id)
			result = void_label(label_id)
			result["parcel_number"] = row.parcel_number
			voided.append(result)
		frappe.db.set_value(
			"Packing Slip Item",
			row.name,
			{"tracking_number": "", "tracking_url": "", "label_url": "", "label_id": ""},
		)

	remove_label_attachments(ps)

	if ps.delivery_note:
		frappe.db.set_value(
			"Delivery Note", ps.delivery_note, {"up_tracking_id": "", "up_tracking_company": ""}
		)

	return voided


def remove_label_attachments(ps) -> int:
	"""Delete the label PDFs a Packing Slip is carrying.

	Without this a void leaves the dead label attached and the re-purchase adds a
	second PDF beside it, so the packer has two labels and no way to tell which one
	the carrier will accept.
	"""
	names = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": ps.doctype,
			"attached_to_name": ps.name,
			"folder": "Home/Shipstation Labels",
		},
		pluck="name",
	)
	for name in names:
		frappe.delete_doc("File", name, force=True, ignore_permissions=True)
	return len(names)


@frappe.whitelist()
def clear_packing_slip_tracking(packing_slip: str) -> dict:
	"""Release a Packing Slip whose labels were voided outside ERP.

	Nothing is sent to the carrier. This only drops our record of the label so a new
	one can be bought, and it is the escape hatch for slips whose rows predate
	label_id storage.
	"""
	ps = frappe.get_doc("Packing Slip", packing_slip)
	cleared = 0
	for row in ps.items or []:
		if not row.get("tracking_number"):
			continue
		frappe.db.set_value(
			"Packing Slip Item",
			row.name,
			{"tracking_number": "", "tracking_url": "", "label_url": "", "label_id": ""},
		)
		cleared += 1

	removed = remove_label_attachments(ps)

	if ps.delivery_note:
		frappe.db.set_value(
			"Delivery Note", ps.delivery_note, {"up_tracking_id": "", "up_tracking_company": ""}
		)

	return {"rows_cleared": cleared, "attachments_removed": removed}


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
	label_responses = []

	# A carrier only prints "1 of N" when the boxes arrive as one shipment, so try
	# that first. Falls back to a label per parcel when the account or the service
	# will not take a multi-package shipment.
	if len(parcel_numbers) > 1:
		label_responses = buy_multi_parcel_labels(
			ps, rate_id, carrier_id, service_code, parcel_numbers
		)

	if not label_responses:
		for parcel_number in parcel_numbers:
			if rate_id:
				label_response = create_label_from_rate(rate_id=rate_id)
			else:
				if not carrier_id or not service_code:
					frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))
					return []
				assert carrier_id is not None and service_code is not None
				shipment_data = build_shipment_from_packing_slip(
					ps, carrier_id, service_code, parcel_number
				)
				label_response = create_label(shipment_data=shipment_data)

			label_response["parcel_number"] = parcel_number
			label_responses.append(label_response)

	for label_response in label_responses:
		if label_response.get("label_download"):
			file_doc = download_and_attach_label(
				label_response["label_download"],
				ps.doctype,
				ps.name,
				settings,
			)
			label_response["attached_file"] = file_doc.name if file_doc else None

		update_packing_slip_tracking(ps, label_response, label_response["parcel_number"])

	update_delivery_note_from_labels(ps, label_responses)

	# Tracking above is written with db.set_value, which skips document events, so a
	# Notification on Delivery Note or Packing Slip would never fire. Raise the notice
	# here, at the point the parcel actually becomes a shipment.
	from upro_erp.uat5_shipment_notice import notify_label_purchased

	notify_label_purchased(ps, label_responses)

	return label_responses


def buy_multi_parcel_labels(
	ps, rate_id: str | None, carrier_id: str | None, service_code: str | None, parcel_numbers: list
) -> list[dict]:
	"""Buy every box on one shipment so the carrier numbers them "1 of N".

	Returns one response per parcel, or an empty list when nothing was bought and
	the caller should fall back to a label per box. A rate is quoted for the whole
	shipment, so buying one never falls back: the money is already spent.
	"""
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

	shipment_data = build_shipment_from_packing_slip(
		ps, carrier_id, service_code, parcel_numbers[0]
	)
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


def carrier_supports_multi_package(carrier_id: str, box_count: int) -> bool:
	"""Whether this carrier account can take every box as one shipment.

	USPS cannot, and neither can GlobalPost or FedEx One Balance. Asking anyway
	costs a rejected request and leaves the packer holding unnumbered labels with
	no idea why, so ask the carrier first and say so when the answer is no.
	"""
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


def labels_from_bought_rate(label_response: dict, parcel_numbers: list) -> list[dict]:
	"""Split a rate that was bought as one shipment into one response per box.

	Buying a rate used to be blocked outright on more than one box, which sent
	the packer back to pick a carrier and service by hand for exactly the
	shipments that most needed numbering.
	"""
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


def split_package_labels(label_response: dict, parcel_numbers: list) -> list[dict]:
	"""One response per box out of a single multi-package purchase.

	Empty when the carrier did not return a label per package, which is the
	signal to buy them one at a time instead.
	"""
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
		download = package_label.get("label_download")
		if isinstance(download, dict):
			download = download.get("pdf") or download.get("href")
		child["label_download"] = download or label_response.get("label_download")
		# The carrier bills the shipment once. Leaving the total on every box would
		# multiply the freight cost written back to the Delivery Note.
		if index:
			child["shipment_cost"] = {}
			child["insurance_cost"] = {}
		responses.append(child)

	return responses


def update_delivery_note_from_labels(ps, label_responses: list[dict]) -> None:
	"""Write tracking and freight cost from purchased labels back to the Delivery Note."""
	if not ps.delivery_note:
		return

	write_tracking_to_delivery_note(ps.delivery_note, label_responses)


def write_tracking_to_delivery_note(dn_name: str, label_responses: list[dict]) -> None:
	"""Write tracking and freight cost from purchased labels back to the Delivery Note.

	Every label path ends here. Customer service, the invoice and the ASN all read
	the Delivery Note, and two of the three paths used to leave it blank.
	"""
	if not dn_name or not label_responses:
		return

	tracking_numbers = [r.get("tracking_number") for r in label_responses if r.get("tracking_number")]
	carrier_code = (label_responses[0].get("carrier_code") or "").upper()
	total_cost = sum(
		flt((r.get("shipment_cost") or {}).get("amount")) + flt((r.get("insurance_cost") or {}).get("amount"))
		for r in label_responses
	)

	values = {}
	if tracking_numbers:
		values["up_tracking_id"] = ", ".join(tracking_numbers)[:140]
	if carrier_code:
		values["up_tracking_company"] = carrier_code
	if total_cost:
		values["up_freight_cost"] = str(round(total_cost, 2))
	if values:
		frappe.db.set_value("Delivery Note", dn_name, values)

	if total_cost:
		add_freight_charge_to_delivery_note(dn_name, round(total_cost, 2))


def add_freight_charge_to_delivery_note(dn_name: str, amount: float) -> None:
	"""
	On "Prepaid & Add" freight terms, put the label cost on the Delivery Note
	taxes table (same "Shipping" row convention as the shipping-rule override)
	so it flows through to the customer invoice.
	"""
	dn = frappe.get_doc("Delivery Note", dn_name)
	if dn.docstatus != 0 or dn.get("up_freight_term") != "Prepaid & Add":
		return

	account, cost_center = frappe.get_value(
		"Freight Account Settings",
		{"sales_channel": dn.up_sales_channel},
		["account_no", "cost_center"],
	) or (None, None)
	if not account:
		frappe.msgprint(
			_(
				"Freight cost {0} was not added to the Delivery Note: no Freight Account "
				"Settings entry for sales channel {1}."
			).format(amount, dn.up_sales_channel),
			indicator="orange",
		)
		return

	existing = [t for t in dn.taxes if t.get("tax_description") == "Shipping"]
	if existing:
		existing[-1].tax_amount = amount
	else:
		dn.append(
			"taxes",
			{
				"charge_type": "Actual",
				"tax_description": "Shipping",
				"description": "Shipping",
				"account_head": account,
				"cost_center": cost_center,
				"tax_amount": amount,
			},
		)
	dn.flags.ignore_permissions = True
	dn.save()


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

	# Target sends the shopper's name on the address; the customer record holds
	# the marketplace account name. Mirror what the packing slip label path
	# already does so both paths put the same name on the parcel.
	ship_to_name = dn.get("up_shopify_customer_name")
	if not ship_to_name and dn.get("up_sales_channel") in TARGET_CHANNELS:
		ship_to_name = ship_to_address.address_title
	ship_to_name = ship_to_name or dn.customer_name or dn.customer

	shipment = {
		"carrier_id": carrier_id,
		"service_code": service_code,
		"ship_to": {
			"name": ship_to_name,
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

	# The customer's freight term decides who the carrier bills, and this path
	# never asked, so a note sold on 3rd party billing was still billed to us.
	third_party = get_third_party_billing_options(
		frappe._dict(), dn, get_supplier_for_carrier_id(carrier_id)
	)
	if third_party:
		shipment["advanced_options"] = third_party

	# The Packing Slip path already stamps the channel and SO number on the label.
	# Labels bought straight off a Delivery Note were going out with a blank
	# reference block, which is what the warehouse reads to find the order.
	messages = get_label_messages(frappe._dict(), dn)
	if messages:
		shipment["label_messages"] = messages

	return shipment


def update_delivery_note_tracking(dn, label_response: dict) -> None:
	"""Update the Delivery Note with label and tracking info.

	This wrote to "tracking_number" and "carrier", neither of which is a field on
	Delivery Note, so it raised after the carrier had already charged us: the
	label existed and nothing recorded it. The Packing Slip path had the right
	fields all along, so use the same ones.
	"""
	write_tracking_to_delivery_note(dn.name, [label_response])


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
		"label_id": label_response.get("label_id") or "",
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
			"packages": label_response.get("packages"),
		}
	return label_response


BILL_TO_PARTY_MAP = {"Receiver": "recipient", "Third Party": "third_party"}


def decline_third_party_billing(dn, bill_to_party: str, reason: str) -> None:
	"""Say why a label is billing our account instead of the customer's.

	This used to be a bare return, so freight sold on the customer's carrier
	account was quietly billed to us and nobody found out until the carrier
	invoice. A Bill To party picked by hand is an error, because somebody asked
	for it; one inferred from the freight term is a warning, because the label is
	still good and the shipment still has to go out.
	"""
	problem = _("{0} is set to bill the customer for freight, but {1}.").format(dn.name, reason)
	if bill_to_party:
		frappe.throw(problem, title=_("Billing Account Not Found"))

	frappe.msgprint(
		problem + " " + _("This label bills {0}.").format(dn.company),
		indicator="orange",
		title=_("Freight Billed to Us"),
	)
	return None


def get_third_party_billing_options(ps, dn=None, carrier: str | None = None) -> dict | None:
	"""
	Return ShipEngine advanced_options for receiver or third-party billing.

	Billing is driven by the Packing Slip's Bill To selector when set, otherwise
	by the Delivery Note freight term: only "3rd Party Billing" auto-applies the
	customer's matching shipping account. "Shipper" returns None and the label
	bills our own carrier account.

	The Packing Slip is optional. The warehouse often skips it and buys the label
	off the Delivery Note or a Shipment, and neither of those paths called this at
	all, so a shipment sold on the customer's account was still billed to us.
	"""
	ps = ps or frappe._dict()
	if dn is None and ps.get("delivery_note"):
		dn = frappe.get_cached_doc("Delivery Note", ps.get("delivery_note"))
	if not dn or not dn.get("customer"):
		return None

	bill_to_party = ps.get("bill_to_party") or ""
	if bill_to_party == "Shipper":
		return None
	if not bill_to_party and dn.get("up_freight_term") != "3rd Party Billing":
		return None

	carrier = carrier or ps.get("carrier")
	if not carrier:
		return decline_third_party_billing(dn, bill_to_party, _("no carrier is set on the shipment"))

	customer = frappe.get_cached_doc("Customer", dn.customer)
	accounts = [row for row in (customer.shipping_accounts or []) if row.carrier == carrier]

	account = None
	if ps.get("bill_to_account"):
		account = next(
			(a for a in accounts if a.shipping_account_number == ps.get("bill_to_account")), None
		)
		if not account:
			frappe.throw(
				_(
					"Account {0} is not on file for customer {1} and carrier {2}. "
					"Add it to the customer's Third Party Shipping Accounts table first."
				).format(ps.get("bill_to_account"), dn.customer, carrier),
				title=_("Billing Account Not Found"),
			)
	else:
		account = (
			next((a for a in accounts if a.default), None)
			or next((a for a in accounts if a.enabled), None)
			or (accounts[0] if accounts else None)
		)

	if not account or not account.shipping_account_number:
		return decline_third_party_billing(
			dn,
			bill_to_party,
			_("customer {0} has no {1} account on file").format(dn.customer, carrier),
		)

	postal_code, country_code = get_billing_postal_code(ps, dn, account)

	options: dict = {
		"bill_to_party": BILL_TO_PARTY_MAP.get(bill_to_party, "third_party"),
		"bill_to_account": account.shipping_account_number,
		"bill_to_country_code": country_code,
	}
	if postal_code:
		options["bill_to_postal_code"] = postal_code

	return options


def get_billing_postal_code(ps, dn, account) -> tuple[str, str]:
	"""
	Resolve the bill-to postal code and verify it against the account on file.

	The postal code stored on the Shipping Account row is authoritative: if the
	Packing Slip carries a different billing zip, the account number and billing
	address don't match and the label purchase is blocked.
	"""
	account_zip = (account.get("billing_postal_code") or "").strip()
	entered_zip = (ps.get("bill_to_postal_code") or "").strip()

	if account_zip and entered_zip and account_zip[:5] != entered_zip[:5]:
		frappe.throw(
			_(
				"Billing zip {0} does not match zip {1} on file for {2} account {3}. "
				"Verify the account number and billing address before purchasing the label."
			).format(entered_zip, account_zip, account.carrier, account.shipping_account_number),
			title=_("Billing Address Mismatch"),
		)

	postal_code = account_zip or entered_zip
	country_code = "US"
	if not postal_code:
		billing_address = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Customer", "link_name": dn.customer, "parenttype": "Address"},
			"parent",
		)
		if billing_address:
			addr = frappe.get_cached_doc("Address", billing_address)
			postal_code = addr.pincode or ""
			country_code = (frappe.db.get_value("Country", addr.country, "code") or "US").upper()

	return postal_code, country_code


# Two channel names for the same Target Plus business on this bench.
TARGET_CHANNELS = ("Target Plus", "EDI-TargetPlus")

# Only the postal services deliver to a PO box; UPS and FedEx hand the parcel back.
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
	"""Target ships on Ultra PRO's UPS account, which does not deliver to PO
	boxes. Stop the label purchase here rather than at the dock."""
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

	validate_po_box_delivery(ship_to_address, service_code, ps.get("carrier"))

	dn = frappe.get_doc("Delivery Note", ps.delivery_note)
	company_name = dn.company
	# Marketplace orders land under a generic customer master ("Shopify",
	# "TargetPlus"); the recipient is stored on the Delivery Note or in the
	# shipping address title.
	customer_name = dn.get("up_shopify_customer_name")
	if not customer_name and dn.get("up_sales_channel") in TARGET_CHANNELS:
		customer_name = frappe.db.get_value("Address", dn.shipping_address_name, "address_title")
	customer_name = customer_name or dn.customer_name or dn.customer

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

	third_party = get_third_party_billing_options(ps, dn)
	if third_party:
		shipment["advanced_options"] = third_party

	label_messages = get_label_messages(ps, dn)
	if label_messages:
		shipment["label_messages"] = label_messages

	return shipment


def get_label_messages(ps, dn) -> dict:
	"""
	Build carrier label reference fields (35 chars max each).

	Defaults: reference1 = sales channel, reference2 = sales order number,
	reference3 = customer PO. Each can be overridden on the Packing Slip.

	Target orders lead with the Target order number instead - that is the
	reference the warehouse and Target's receiving dock both scan against.
	"""
	so_number = dn.get("up_sales_order_number") or next(
		(item.against_sales_order for item in (dn.items or []) if item.against_sales_order), ""
	)
	channel = dn.get("up_sales_channel") or ""
	customer_po = dn.get("po_no") or ""
	target_order_no = customer_po if channel in TARGET_CHANNELS else ""

	references = {
		"reference1": ps.get("label_reference_1") or target_order_no or channel or so_number,
		"reference2": ps.get("label_reference_2") or so_number,
		"reference3": ps.get("label_reference_3") or (channel if target_order_no else customer_po),
	}

	return {key: str(value)[:35] for key, value in references.items() if value}


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
	label_responses = []

	# Same reason as the Packing Slip path: a carrier only numbers boxes when they
	# reach it as one shipment, and this bought them one request at a time.
	if len(parcel_numbers) > 1:
		label_responses = buy_multi_parcel_shipment_labels(
			doc, rate_id, carrier_id, service_code, parcel_numbers
		)

	if not label_responses:
		for parcel_number in parcel_numbers:
			if rate_id:
				label_response = create_label_from_rate(rate_id=rate_id)
			else:
				if not carrier_id or not service_code:
					frappe.throw(_("Either rate_id or both carrier_id and service_code are required"))
					return []
				assert carrier_id is not None and service_code is not None
				shipment_data = build_shipment_from_shipment_doc(
					doc, carrier_id, service_code, parcel_number
				)
				label_response = create_label(shipment_data=shipment_data)

			label_response["parcel_number"] = parcel_number
			label_responses.append(label_response)

	for label_response in label_responses:
		if label_response.get("label_download"):
			file_doc = download_and_attach_label(
				label_response["label_download"],
				doc.doctype,
				doc.name,
				settings,
			)
			label_response["attached_file"] = file_doc.name if file_doc else None

		update_shipment_delivery_note_tracking(doc, label_response, label_response["parcel_number"])

	# The PRO number used to stop at the Shipment. The invoice, the ASN and anyone
	# answering "where is my order" all read the Delivery Note.
	for dn_name in sorted(
		{row.delivery_note for row in (doc.shipment_delivery_note or []) if row.delivery_note}
	):
		parcels = {
			row.parcel_number for row in doc.shipment_delivery_note if row.delivery_note == dn_name
		}
		write_tracking_to_delivery_note(
			dn_name, [r for r in label_responses if r.get("parcel_number") in parcels]
		)

	return label_responses


def buy_multi_parcel_shipment_labels(
	doc,
	rate_id: str | None,
	carrier_id: str | None,
	service_code: str | None,
	parcel_numbers: list,
) -> list[dict]:
	"""Buy every box on a Shipment as one carrier shipment, so they are numbered.

	Empty when nothing was bought and the caller should fall back to a label per
	box.
	"""
	if rate_id:
		return labels_from_bought_rate(create_label_from_rate(rate_id=rate_id), parcel_numbers)

	if not carrier_id or not service_code:
		return []
	if not carrier_supports_multi_package(carrier_id, len(parcel_numbers)):
		return []

	# Build each box through the single-parcel builder rather than repeat here how
	# a Shipment describes a package. Only the package list differs between them.
	shipment_data = build_shipment_from_shipment_doc(doc, carrier_id, service_code, parcel_numbers[0])
	packages = []
	for parcel_number in parcel_numbers:
		per_parcel = build_shipment_from_shipment_doc(doc, carrier_id, service_code, parcel_number)
		packages.extend(per_parcel.get("packages") or [])

	if len(packages) != len(parcel_numbers):
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
		return {}

	# The Shipment has no Packing Slip, so pull the references off the first
	# Delivery Note on the shipment and let get_label_messages apply the same
	# channel / sales order / customer PO defaults the parcel path uses.
	# The same Delivery Note also decides who the carrier bills. This path never
	# asked, so freight sold on the customer's account was billed to us.
	label_messages = {}
	third_party = None
	dn_name = next(
		(row.delivery_note for row in (doc.shipment_delivery_note or []) if row.delivery_note), None
	)
	if dn_name:
		dn = frappe.get_doc("Delivery Note", dn_name)
		label_messages = get_label_messages(frappe._dict(), dn)
		third_party = get_third_party_billing_options(
			frappe._dict(), dn, get_supplier_for_carrier_id(carrier_id)
		)

	payload = {
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

	if label_messages:
		payload["label_messages"] = label_messages
	if third_party:
		payload["advanced_options"] = third_party

	return payload


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
