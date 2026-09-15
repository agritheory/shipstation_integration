# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Billing and reference options for Packing Slip labels.

Callers use ``resolve_label_billing_options`` and ``resolve_label_reference``.
Other apps override those hooks; this module supplies the shipstation defaults.
"""

from __future__ import annotations

import frappe

COLLECT_OR_THIRD_PARTY = frozenset({"collect", "third party", "third_party"})


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
	"""Read freight terms from the linked Sales Order when that field exists."""
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_first_sales_order_from_packing_slip,
	)

	so_name = get_first_sales_order_from_packing_slip(packing_slip)
	if not so_name:
		return None
	so = frappe.get_doc("Sales Order", so_name)
	return billing_options_from_freight_terms(packing_slip, so.get("payment_terms"))


def default_label_reference(packing_slip) -> str:
	"""Use the linked Sales Order name, or empty when none is linked."""
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_first_sales_order_from_packing_slip,
	)

	return get_first_sales_order_from_packing_slip(packing_slip) or ""


def billing_options_from_freight_terms(packing_slip, payment_terms) -> dict | None:
	"""Map freight terms to ShipEngine advanced_options.

	Prepaid or empty bills the shipper (None). Collect and Third Party use the
	customer's shipping account for the packing slip carrier. A customer
	shipping account alone does not imply third-party billing.
	"""
	terms = (payment_terms or "").strip().lower()
	if terms not in COLLECT_OR_THIRD_PARTY:
		return None
	return shipping_account_billing_options(packing_slip)


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
