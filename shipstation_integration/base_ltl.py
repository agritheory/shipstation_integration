# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from erpnext.stock.doctype.shipment.shipment import Shipment
from frappe import _


def require_submitted_shipment_for_ltl(doc: Shipment) -> None:
	"""LTL quotes and booking run after the Shipment is submitted (packed and locked)."""
	docstatus = (
		doc.docstatus
		if doc.docstatus is not None
		else frappe.db.get_value("Shipment", doc.name, "docstatus")
	)
	if docstatus != 1:
		frappe.throw(
			_("Submit the Shipment before requesting LTL quotes or scheduling pickup."),
			title=_("Shipment not submitted"),
		)


class BaseLTL:
	def book_shipment(self, doc, settings_name: str | None = None) -> dict:
		"""
		Convert an accepted quote into a booked/dispatched shipment.
		Returns dict with at minimum: pro_number, bol_number, and any generated document data.
		For ShipEngine this wraps schedule_ltl_pickup. For brokers, this awards a quote and dispatches.
		For direct carriers, this creates an eBOL.
		"""
		raise NotImplementedError

	def cancel_shipment(self, doc, settings_name: str | None = None) -> str | None:
		"""Cancel a booked shipment or scheduled pickup. Returns status message."""
		raise NotImplementedError

	def track_shipment(self, doc, settings_name: str | None = None) -> dict:
		"""Returns tracking status for the shipment. Dict should contain at minimum: status (str), events (list of dicts)."""
		raise NotImplementedError

	def get_documents(self, doc, settings_name: str | None = None) -> list[dict]:
		"""Returns list of available documents. Each dict has: type (str), image (base64 str), format (str)."""
		raise NotImplementedError

	# existing methods follow

	def __init__(self):
		"""
		The self.provider value should be the name of the API service provider
		"""
		self.provider = ""

	def list_ltl_carriers(
		self,
		settings_name: str | None = None,
		create_transporters: bool = False,
		company: str | None = None,
		supplier: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		"""
		List all LTL carriers connected to the provider account. Returns a list of carrier dict
		objects. The results are used to store and display LTL carrier data in Shipstation
		Settings.

		Auth: Freight Carrier Settings for (company, supplier) or Shipment doc — not Shipstation Settings.

		Front end expects each carrier dict to have the following format:
		{
		    "name": "",
		    "carrier_id": "",
		    "carrier_code": "",  # carrier's SCAC
		    "options": [],  # optional list of accessorial services (only length used)
		    "services": [],  # optional list of services (only length used)
		    "packages": [],  # optional list of dicts ("code", "name", "package_features" keys used) for package/container types.
		    "supplier": "",  # optional, Supplier name in ERPNext for carrier
		}

		If the provider doesn't connect carriers to an account and uses a different model, return
		an empty list.

		Args:
		settings_name: Deprecated / unused for default implementation
		create_transporters: If True, create Supplier records with is_transporter=1 for each
		carrier that doesn't already exist
		company: Company for Freight Carrier Settings
		supplier: Supplier (transporter) for Freight Carrier Settings
		doc: Optional Shipment

		Returns:
		List of carrier dicts with carrier_id, carrier_code, name, supplier, etc.
		"""
		raise NotImplementedError

	def get_carrier_id_for_supplier(
		self,
		supplier_name: str,
		settings_name: str | None = None,
		company: str | None = None,
	) -> str | None:
		"""
		Retrieves the provider's carrier_id for a given Supplier (transporter) name. This may be
		stored in the ltl_carrier_id field in the Supplier record or within the synced LTL carrier
		JSON stored in the Shipstation Settings shipstation_api_ltl_carrier_data field.

		Args:
		supplier_name: The Supplier document name (e.g., "UPS", "USPS")
		settings_name: Optional Shipstation Settings document name
		company: Optional company for Freight Carrier Settings context

		Returns:
		Provider's carrier_id (e.g., "100abcde-...") or None if not found
		"""
		raise NotImplementedError

	def get_package_type_options(
		self,
		carrier_id: str | None = None,
		settings_name: str | None = None,
		doc: Shipment | None = None,
	) -> list[dict]:
		"""
		Returns a UI-friendly dict with label and value keys to populate dropdown options in the
		Shipment document's Shipment Parcel child table package_type field.

		Args:
		carrier_id: The provider's LTL carrier ID, if used (e.g., "100abcde-...")
		settings_name: Optional Shipstation Settings document name
		doc: Optional Shipment context for Freight Carrier Settings resolution

		Returns:
		List of dicts with "value" and "label" keys for use in select field
		Example: [{"value": "plt", "label": "Pallet"}, ...]
		"""
		raise NotImplementedError

	def get_carrier_service_levels(
		self, doc: Shipment, settings_name: str | None = None
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
		raise NotImplementedError

	def get_accessorial_service_fields(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""
		Returns a dict of field names for supported and unsupported accessorial services - may be
		carrier-dependent or in general.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Dict with keys for "supported" and "unsupported" lists, that contain the Shipment document
		field names for supported and unsupported accessorial services.
		Field names can be found under Customize Form -> Shipment
		"""
		raise NotImplementedError

	def get_shipment_dimension_uoms(self) -> dict:
		"""
		UOM options for package length, weight, and density. These are used to limit UI options in
		the shipment parcel table. If the list is empty, no filter is set for that field.

		Returns:
		Dict with "length_uom", "weight_uom", and "density_uom" keys. Each value is a list of
		ERPNext UOMs the API payloads can accept
		"""
		return {"length_uom": [], "weight_uom": [], "density_uom": []}

	def validate_required_shipment_form_fields(
		self, doc: Shipment, settings_name: str | None = None
	) -> str | None:
		"""
		Returns a message to display in the UI noting any fields that are required to make a
		'get_ltl_quotes' API call that are missing data. It can skip fields that are already
		marked required.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Message string to display in UI
		"""
		raise NotImplementedError

	def supports_quote_or_spot_quote(self, doc: Shipment, settings_name: str | None = None) -> dict:
		"""
		Convenience function that returns True/False whether a carrier in a Shipment doc (or the
		API in general) supports requesting quotes and/or spot quotes.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Dict with keys for "supports_quote" and "supports_spot_quote" with boolean values
		"""
		return {"supports_quote": False, "supports_spot_quote": False}

	def get_ltl_quotes(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""
		Gets LTL quote(s) in general or for a specific LTL carrier given Shipment data. If found,
		saves into Shipment Quotation docs and returns a summary message. Otherwise, displays
		message explaining no quotes or throws an error in the process.

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Message string to display in UI or throws an error if encountered
		"""
		raise NotImplementedError

	def fetch_ltl_offers(self, doc: Shipment, settings_name: str | None = None) -> list[dict]:
		"""Return available LTL offers as normalized dicts without persisting. Override for quote-shop UIs."""
		raise NotImplementedError

	# def supports_scheduled_pickup(
	# 	self, doc: Shipment, settings_name: str | None = None
	# ) -> dict:
	# 	"""
	# 	Convenience function that returns dict with True/False whether a specific carrier (or the
	# 	API in general) supports electronically scheduling a pickup.

	# 	Args:
	# 	doc: a Shipment document in ERPNext
	# 	settings_name: Optional Shipstation Settings document name

	# 	Returns:
	# 	Dict with key for "supports_pickup" with boolean value
	# 	"""
	# 	return {"supports_pickup": False}

	def schedule_ltl_pickup(self, doc: Shipment, settings_name: str | None = None) -> str | None:
		"""
		Schedules LTL pickup with quote ID(s) saved in doc. If successful, sets fields in the
		Shipment Information section (if available in the response):
		- carrier and carrier service
		- pickup ID, pickup transaction ID (extra field if needed), and shipment ID
		- the awb_number field with the tracking/PRO number
		- the shipment amount
		- attach BOL to the Shipment doc

		Args:
		doc: a Shipment document in ERPNext
		settings_name: Optional Shipstation Settings document name

		Returns:
		Message string to display in UI or throws an error if encountered
		"""
		raise NotImplementedError
