# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import json

import frappe
from erpnext.stock.doctype.shipment.shipment import Shipment
from frappe.exceptions import ValidationError

from shipstation_integration.utils import get_shipstation_settings


class ShipStationShipment(Shipment):
	def validate(self):
		# TODO: if freight_type == "LTL" -> call ltl_class method to show missing but required fields
		super().validate()


@frappe.whitelist()
def get_carrier_id_for_supplier(
	supplier_name: str, settings_name: str | None = None
) -> str | None:
	"""
	Returns the provider-specific carrier ID for a given Supplier in ERPNext.

	Args:
	supplier_name: name of a Supplier in ERPNext
	settings_name: Optional Shipstation Settings document name

	Returns:
	Carrier ID if available
	"""
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	return ltl_class.get_carrier_id_for_supplier(supplier_name, settings.name)


@frappe.whitelist()
def get_ltl_package_type_options(
	carrier_id: str | None = None, settings_name: str | None = None
) -> list:
	"""
	Returns a UI-friendly dict with label and value keys to populate dropdown options in the
	Shipment document's Shipment Parcel child table package_type field.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of dicts with "value" and "label" keys for use in select field
	"""
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	return ltl_class.get_package_type_options(carrier_id, settings.name)


@frappe.whitelist()
def get_shipment_dimension_uoms(settings_name: str | None = None) -> dict:
	"""
	UOM options for package length, weight, and density for Shipment Parcel table fields.

	Args:
	settings_name: Optional Shipstation Settings document name

	Returns:
	Dict with "length_uom", "weight_uom", and "density_uom" keys. Each value is a list of ERPNext
	UOMs the API payloads can accept
	"""
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	return ltl_class.get_shipment_dimension_uoms()


@frappe.whitelist()
def get_carrier_service_levels(
	doc: Shipment | str, settings_name: str | None = None
) -> list[dict]:
	"""
	Returns a UI-friendly dict with label and value keys to populate dropdown options in the
	Shipment document's carrier_service_level field.

	Args:
	doc: a Shipment document in ERPNext
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of dicts with "value" and "label" keys for use in select field
	"""
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	return ltl_class.get_carrier_service_levels(doc, settings.name)


@frappe.whitelist()
def get_supported_accessorial_service_fields(
	doc: Shipment | str, settings_name: str | None = None
) -> dict[str, list[str]]:
	"""
	Returns a list of field names for supported accessorial services - may be carrier-
	dependent or in general.

	Args:
	doc: a Shipment document in ERPNext
	settings_name: Optional Shipstation Settings document name

	Returns:
	List of the Shipment document field names for supported accessorial services.
	"""
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	return ltl_class.get_accessorial_service_fields(doc, settings.name)


@frappe.whitelist()
def supports_quote_or_spot_quote(doc: Shipment | str, settings_name: str | None = None) -> list:
	"""
	Convenience function that returns True/False whether a carrier in a Shipment doc (or the API
	in general) supports requesting quotes and/or spot quotes.

	Args:
	doc: a Shipment document in ERPNext
	settings_name: Optional Shipstation Settings document name

	Returns:
	Dict with keys for "supports_quote" and "supports_spot_quote" with boolean values
	"""
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	return ltl_class.supports_quote_or_spot_quote(doc, settings.name)


@frappe.whitelist()
def get_ltl_quotes(doc: Shipment | str, settings_name: str | None = None) -> str | None:
	"""
	Gets LTL quote(s) in general or for a specific LTL carrier given Shipment data. If found,
	saves into Shipment Quotation docs and returns a summary message. Otherwise, displays message
	explaining no quotes or throws an error in the process.

	Args:
	doc: a Shipment document in ERPNext
	settings_name: Optional Shipstation Settings document name

	Returns:
	Message string to display in UI
	"""
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	return ltl_class.get_ltl_quotes(doc, settings.name)


# @frappe.whitelist()
# def supports_scheduled_pickup(doc: Shipment | str, settings_name: str | None = None) -> dict:
# 	"""
# 	Convenience function that returns dict with True/False whether a specific carrier (or the
# 	API in general) supports electronically scheduling a pickup.

# 	Args:
# 	doc: a Shipment document in ERPNext
# 	settings_name: Optional Shipstation Settings document name

# 	Returns:
# 	Dict with key for "supports_pickup" with boolean value
# 	"""
# 	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
# 	settings = get_shipstation_settings(settings_name)
# 	ltl_class = settings.get_ltl_class()
# 	return ltl_class.supports_scheduled_pickup(doc, settings.name)


@frappe.whitelist()
def schedule_ltl_pickup(doc: Shipment | str, settings_name: str | None = None) -> str | None:
	"""
	Schedules LTL pickup given the quote ID field(s) are set. If successful, sets the pickup ID
	field(s), and if they're available in the response:
	- set estimated delivery date
	- set the awb_number field with the tracking/PRO number
	- attach BOL to the Shipment doc

	Args:
	doc: a Shipment document in ERPNext
	settings_name: Optional Shipstation Settings document name

	Returns:
	Message string to display in UI
	"""
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	settings = get_shipstation_settings(settings_name)
	ltl_class = settings.get_ltl_class()
	message = ltl_class.schedule_ltl_pickup(doc, settings.name)
	# TODO: if successful, save shipping amount to DN and submit?
	return message
