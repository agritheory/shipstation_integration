# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Shipment DocType override and whitelisted desk/API helpers.

``ShipStationShipment`` is registered in hooks.py via ``override_doctype_class``. Whitelisted
methods that operate on Shipment + LTL are kept in the same module so they sit next to that
class without a separate API package. The dotted path is long because the app uses a nested
``shipstation_integration`` package directory.

REST API: call these with **POST** (not GET). Pass the Shipment as a JSON string in the form
field ``doc`` (and optional ``settings_name``), matching ``frappe.call({ method, args })`` from
the desk.
"""

import json
from datetime import datetime, timedelta
from typing import Any

import frappe
from erpnext.stock.doctype.shipment.shipment import Shipment
from frappe import _
from frappe.utils import get_time
from inventory_tools.inventory_tools.overrides.pack_stock_reservation import (
	cancel_stock_reservation_entries_from_pack,
	maybe_reserve_stock_on_pack_submit,
)
from inventory_tools.inventory_tools.overrides.shipment import InventoryToolsShipment

from shipstation_integration.base_ltl import require_submitted_shipment_for_ltl
from shipstation_integration.incoterms import populate_shipment_incoterm
from shipstation_integration.ltl import get_ltl_provider
from shipstation_integration.shipstation_integration.overrides.handling_unit import (
	on_shipment_submit,
)
from shipstation_integration.utils import get_shipstation_settings_optional


def ltl_settings_name(settings_name: str | None) -> str | None:
	settings = get_shipstation_settings_optional(settings_name)
	return settings.name if settings else None


MINIMUM_PICKUP_WINDOW = timedelta(minutes=15)


class ShipStationShipment(InventoryToolsShipment):
	def ensure_shipment_parcel_dimension_uoms(self):
		"""Default parcel UOMs before mandatory validation (ASW defers them until link-back save)."""
		for parcel in self.get("shipment_parcel") or []:
			if not parcel.get("length_uom"):
				parcel.length_uom = "Inch"
			if not parcel.get("weight_uom"):
				parcel.weight_uom = "Pound"

	def validate(self):
		self.ensure_shipment_parcel_dimension_uoms()
		if self.get("freight_type") == "LTL":
			populate_shipment_incoterm(self)
			if not self.get("delivery_contact_name"):
				frappe.throw(
					_("Delivery Contact is required for LTL shipments."),
					title=_("Delivery contact required"),
				)
			self.normalize_pickup_window()
		# TODO: if freight_type == "LTL" -> call ltl_class method to show missing but required fields
		super().validate()

	def normalize_pickup_window(self) -> None:
		"""Restore the default pickup window when it is too narrow to dispatch against."""
		start, end = self.get("pickup_from"), self.get("pickup_to")
		if start and end:
			opens = datetime.combine(datetime.min, get_time(start))
			closes = datetime.combine(datetime.min, get_time(end))
			if closes - opens >= MINIMUM_PICKUP_WINDOW:
				return
		meta = frappe.get_meta("Shipment")
		self.pickup_from = meta.get_field("pickup_from").default or "09:00:00"
		self.pickup_to = meta.get_field("pickup_to").default or "17:00:00"

	def before_submit(self):
		"""
		Validate that every Shipment Delivery Note item has been assigned to a
		parcel before the Shipment is submitted.

		This validation only applies when the SDN table has item-level rows
		(i.e. any row has dn_detail or item_code populated). Pure DN-link rows
		without item details are allowed through unpacked.
		"""
		if self.uses_alternative_sales_workflow_without_delivery_note():
			if not any(row.parcel_number for row in (self.shipment_delivery_note or [])):
				return

		item_level_rows = [
			row
			for row in (self.shipment_delivery_note or [])
			if row.get("item_code") or row.get("dn_detail")
		]
		if not item_level_rows:
			return

		unpacked = [row for row in item_level_rows if not row.parcel_number]
		if unpacked:
			frappe.throw(
				_(
					"All Shipment Delivery Note items must be assigned to a parcel before submitting. "
					"{0} item(s) are not yet packed."
				).format(len(unpacked))
			)

	def on_submit(self):
		# Shipstation packs on shipment_delivery_note; shipment_parcel is hidden and unused.
		# Do not call super().on_submit() — ERPNext requires shipment_parcel; Inventory Tools
		# super() would invoke that check. Compose status, reservation, and HU explicitly.
		if self.value_of_goods == 0:
			frappe.throw(_("Value of goods cannot be 0"))
		self.db_set("status", "Submitted")
		maybe_reserve_stock_on_pack_submit(self, "Shipment")
		if self.get("reserve_stock_on_submit"):
			self.db_set("reserve_stock_on_submit", 0)
		on_shipment_submit(self)

	def on_cancel(self):
		cancel_stock_reservation_entries_from_pack("Shipment", self.name, notify=False)
		super().on_cancel()


@frappe.whitelist()
def get_ltl_carrier_suppliers() -> list[str]:
	"""Suppliers with an enabled Freight Carrier Settings row."""
	rows = frappe.db.get_all("Freight Carrier Settings", filters={"disabled": 0}, fields=["supplier"])
	return sorted({row.supplier for row in rows if row.supplier})


@frappe.whitelist()
def make_shipment_from_dn(source_name: str, target_doc=None):
	"""Create a Shipment from a Delivery Note in any open state."""
	from frappe.contacts.doctype.contact.contact import get_default_contact
	from frappe.model.mapper import get_mapped_doc

	def postprocess(source, target):
		user = frappe.db.get_value(
			"User", frappe.session.user, ["email", "full_name", "phone", "mobile_no"], as_dict=1
		)
		target.pickup_contact_email = user.email
		pickup_contact_display = f"{user.full_name}"
		if user:
			if user.email:
				pickup_contact_display += "<br>" + user.email
			if user.phone:
				pickup_contact_display += "<br>" + user.phone
			if user.mobile_no and not user.phone:
				pickup_contact_display += "<br>" + user.mobile_no
		target.pickup_contact = pickup_contact_display
		target.pickup_contact_person = frappe.session.user
		contact_person = source.contact_person or get_default_contact("Customer", source.customer)
		if contact_person:
			contact = frappe.db.get_value(
				"Contact", contact_person, ["email_id", "phone", "mobile_no"], as_dict=1
			)
			delivery_contact_display = source.contact_display or contact_person or ""
			if contact and not source.contact_display:
				if contact.email_id:
					delivery_contact_display += "<br>" + contact.email_id
				if contact.phone:
					delivery_contact_display += "<br>" + contact.phone
				if contact.mobile_no and not contact.phone:
					delivery_contact_display += "<br>" + contact.mobile_no
			target.delivery_contact_name = contact_person
			if contact and contact.email_id and not target.delivery_contact_email:
				target.delivery_contact_email = contact.email_id
			target.delivery_contact = delivery_contact_display
		if source.shipping_address_name:
			target.delivery_address_name = source.shipping_address_name
			target.delivery_address = source.shipping_address
		elif source.customer_address:
			target.delivery_address_name = source.customer_address
			target.delivery_address = source.address_display

	return get_mapped_doc(
		"Delivery Note",
		source_name,
		{
			"Delivery Note": {
				"doctype": "Shipment",
				"field_map": {
					"grand_total": "value_of_goods",
					"company": "pickup_company",
					"company_address": "pickup_address_name",
					"company_address_display": "pickup_address",
					"customer": "delivery_customer",
					"contact_person": "delivery_contact_name",
					"contact_email": "delivery_contact_email",
				},
				"validation": {"docstatus": ["<", 2]},
			},
			"Delivery Note Item": {
				"doctype": "Shipment Delivery Note",
				"field_map": {
					"name": "prevdoc_detail_docname",
					"parent": "prevdoc_docname",
					"parenttype": "prevdoc_doctype",
					"base_amount": "grand_total",
				},
			},
		},
		target_doc,
		postprocess,
	)


@frappe.whitelist()
def get_carrier_id_for_supplier(
	supplier_name: str, settings_name: str | None = None, company: str | None = None
) -> str | None:
	"""
	Returns the provider-specific carrier ID for a given Supplier in ERPNext.

	Args:
	supplier_name: name of a Supplier in ERPNext
	settings_name: Optional Shipstation Settings document name
	company: Optional Company for Freight Carrier Settings context

	Returns:
	Carrier ID if available
	"""
	if company is None:
		company = frappe.defaults.get_user_default("Company")
	# The form calls this with no Shipment, so get_ltl_provider() would always
	# fall back to ShipstationLTL. Hand it enough of a doc to resolve FCS.
	context = frappe._dict(
		preferred_carrier=supplier_name,
		pickup_from_type="Company",
		pickup_company=company,
	)
	ltl_class = get_ltl_provider(context)
	return ltl_class.get_carrier_id_for_supplier(
		supplier_name, ltl_settings_name(settings_name), company
	)


@frappe.whitelist()
def get_ltl_package_type_options(
	carrier_id: str | None = None,
	settings_name: str | None = None,
	shipment: str | None = None,
) -> list:
	"""
	Returns a UI-friendly dict with label and value keys to populate dropdown options in the
	Shipment document's Shipment Parcel child table package_type field.

	Args:
	carrier_id: The ShipEngine LTL carrier ID (e.g., "100abcde-...")
	settings_name: Optional Shipstation Settings document name
	shipment: Optional JSON Shipment fields for Freight Carrier Settings API key resolution

	Returns:
	List of dicts with "value" and "label" keys for use in select field
	"""
	doc = None
	if shipment:
		parsed = json.loads(shipment) if isinstance(shipment, str) else shipment
		if isinstance(parsed, dict):
			doc = frappe._dict(parsed)
	ltl_class = get_ltl_provider(doc)
	return ltl_class.get_package_type_options(carrier_id, ltl_settings_name(settings_name), doc)


@frappe.whitelist()
def get_shipment_dimension_uoms(settings_name: str | None = None) -> dict:
	"""
	UOM options for package length, weight, and density for Shipment Parcel table fields.

	Args:
	settings_name: Optional Shipstation Settings document name (unused; LTL UOMs do not require API)

	Returns:
	Dict with "length_uom", "weight_uom", and "density_uom" keys. Each value is a list of ERPNext
	UOMs the API payloads can accept
	"""
	ltl_class = get_ltl_provider()
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
	ltl_class = get_ltl_provider(doc)
	return ltl_class.get_carrier_service_levels(doc, ltl_settings_name(settings_name))


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
	ltl_class = get_ltl_provider(doc)
	return ltl_class.get_accessorial_service_fields(doc, ltl_settings_name(settings_name))


@frappe.whitelist()
def supports_quote_or_spot_quote(doc: Shipment | str, settings_name: str | None = None) -> dict:
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
	ltl_class = get_ltl_provider(doc)
	return ltl_class.supports_quote_or_spot_quote(doc, ltl_settings_name(settings_name))


@frappe.whitelist()
def fetch_ltl_quotes(doc: Shipment | str, settings_name: str | None = None) -> list[dict]:
	"""Return available LTL quotes as a list of normalized dicts without saving anything.

	The JS layer uses this to populate a selection dialog; the user then calls
	``save_selected_ltl_quotes`` with only the quotes they want to keep.

	Args:
	doc: Shipment dict or JSON string.
	settings_name: Optional Shipstation Settings document name.

	Returns:
	List of quote dicts (carrier_name, carrier_scac, offer_id, transaction_id,
	service_level, total_price, currency, transit_days, estimated_delivery_date,
	expiration_date, is_spot_quote, charges).
	"""
	if not doc:
		frappe.throw(_("Missing Shipment data: pass ``doc`` as a JSON string in the POST body."))
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	ltl_class = get_ltl_provider(doc)
	return ltl_class.fetch_ltl_offers(doc, ltl_settings_name(settings_name))


@frappe.whitelist()
def save_selected_ltl_quotes(shipment_name: str, selected_quotes: list | str) -> str:
	"""Save only the quotes the user selected from the dialog as Shipment Quotation docs.

	Args:
	shipment_name: Name of the Shipment document.
	selected_quotes: JSON list of normalized quote dicts returned by ``fetch_ltl_quotes``.

	Returns:
	Summary message string.
	"""
	parsed = json.loads(selected_quotes) if isinstance(selected_quotes, str) else selected_quotes
	if not isinstance(parsed, list):
		frappe.throw(_("Invalid quotes payload: expected a JSON array."))
	if not parsed:
		frappe.throw(_("No quotes selected."))

	doc = frappe.get_doc("Shipment", shipment_name)
	require_submitted_shipment_for_ltl(doc)
	saved = 0
	for raw in parsed:
		if not isinstance(raw, dict):
			frappe.throw(_("Each selected quote must be a JSON object."))
		q: dict[str, Any] = raw
		scac = q.get("carrier_scac") or ""
		carrier_name = q.get("carrier_name") or "Unknown"

		supplier_name = (
			frappe.db.get_value("Supplier", {"ltl_carrier_scac": scac, "is_transporter": 1}, "name")
			if scac
			else None
		)

		sq = frappe.new_doc("Shipment Quotation")
		sq.shipment = doc.name
		sq.carrier = supplier_name or carrier_name
		sq.carrier_scac = scac
		sq.quote_or_offer_id = q.get("offer_id") or ""
		sq.quote_or_offer_transaction_id = q.get("transaction_id") or ""
		sq.service_level = q.get("service_level") or ""
		sq.grand_total = float(q.get("total_price") or 0)
		sq.pickup_date = doc.get("pickup_date")
		sq.is_spot_quote = bool(q.get("is_spot_quote"))
		if q.get("transit_days") is not None:
			sq.estimated_delivery_days = float(q["transit_days"])
		if q.get("estimated_delivery_date"):
			sq.estimated_delivery_date = q["estimated_delivery_date"]
		if q.get("expiration_date"):
			try:
				sq.expiration_date = q["expiration_date"][:10]
			except Exception:
				pass
		for raw_charge in q.get("charges") or []:
			if not isinstance(raw_charge, dict):
				continue
			charge: dict[str, Any] = raw_charge
			amount_obj = charge.get("amount")
			amount_inner = amount_obj if isinstance(amount_obj, dict) else {}
			c_type = charge.get("type", "N/A").title()
			amount = float(amount_inner.get("value", 0))
			if c_type.lower() == "discount":
				amount *= -1
			if c_type.lower() == "total":
				continue
			sq.append(
				"charges",
				{
					"type": c_type,
					"amount": amount,
					"currency": amount_inner.get("currency") or "USD",
					"description": charge.get("description") or "",
				},
			)
		sq.insert(ignore_permissions=True)
		saved += 1

	return _("{0} quote(s) saved as Shipment Quotation(s).").format(saved)


@frappe.whitelist()
def get_ltl_quotes(doc: Shipment | str, settings_name: str | None = None) -> str | None:
	"""
	Gets LTL quote(s) in general or for a specific LTL carrier given Shipment data. If found,
	saves into Shipment Quotation docs and returns a summary message. Otherwise, displays message
	explaining no quotes or throws an error in the process.

	Args:
	doc: Shipment dict or JSON string (use POST; include ``delivery_contact_name`` for LTL).
	settings_name: Optional Shipstation Settings document name

	Returns:
	Message string to display in UI
	"""
	if not doc:
		frappe.throw(_("Missing Shipment data: pass ``doc`` as a JSON string in the POST body."))
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	ltl_class = get_ltl_provider(doc)
	return ltl_class.get_ltl_quotes(doc, ltl_settings_name(settings_name))


@frappe.whitelist()
def schedule_ltl_pickup(doc: Shipment | str, settings_name: str | None = None) -> str | None:
	"""
	Schedules LTL pickup given the quote ID field(s) are set. If successful, sets the pickup ID
	field(s), and if they're available in the response:
	- set estimated delivery date
	- set the awb_number field with the tracking/PRO number
	- attach BOL to the Shipment doc

	Args:
	doc: Shipment dict or JSON string (use POST; include ``delivery_contact_name`` for LTL).
	settings_name: Optional Shipstation Settings document name

	Returns:
	Message string to display in UI
	"""
	if not doc:
		frappe.throw(_("Missing Shipment data: pass ``doc`` as a JSON string in the POST body."))
	doc = frappe.get_doc(json.loads(doc)) if isinstance(doc, str) else doc
	ltl_class = get_ltl_provider(doc)
	message = ltl_class.schedule_ltl_pickup(doc, ltl_settings_name(settings_name))
	# TODO: if successful, save shipping amount to DN and submit?
	return message
