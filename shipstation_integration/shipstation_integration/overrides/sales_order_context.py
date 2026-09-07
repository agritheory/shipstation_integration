# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Resolve company, customer, and warehouse from Sales Order when no Delivery Note exists."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.contacts.doctype.address.address import get_default_address


def get_first_sales_order_from_pack_lines(rows) -> str | None:
	for row in rows or []:
		if row.get("against_sales_order"):
			return row.against_sales_order
	return None


def get_first_sales_order_from_packing_slip(ps) -> str | None:
	return get_first_sales_order_from_pack_lines(ps.get("items"))


def get_company_from_packing_slip(ps) -> str | None:
	if ps.get("delivery_note"):
		return frappe.db.get_value("Delivery Note", ps.delivery_note, "company")
	so_name = get_first_sales_order_from_packing_slip(ps)
	if so_name:
		return frappe.db.get_value("Sales Order", so_name, "company")
	return frappe.defaults.get_user_default("Company")


def get_customer_from_packing_slip(ps) -> tuple[str | None, str | None]:
	if ps.get("delivery_note"):
		dn = frappe.db.get_value(
			"Delivery Note",
			ps.delivery_note,
			["customer", "customer_name"],
			as_dict=True,
		)
		if dn:
			return dn.customer, dn.customer_name or dn.customer
	so_name = get_first_sales_order_from_packing_slip(ps)
	if so_name:
		so = frappe.db.get_value(
			"Sales Order",
			so_name,
			["customer", "customer_name"],
			as_dict=True,
		)
		if so:
			return so.customer, so.customer_name or so.customer
	return None, None


def get_company_dispatch_address(company: str) -> str | None:
	return get_default_address("Company", company)


def apply_packing_slip_addresses_from_sales_order(ps, sales_order_name: str | None = None) -> None:
	so_name = sales_order_name or get_first_sales_order_from_packing_slip(ps)
	if not so_name:
		return

	so = frappe.get_doc("Sales Order", so_name)
	if not ps.get("shipping_address_name") and so.shipping_address_name:
		ps.shipping_address_name = so.shipping_address_name

	if not ps.get("dispatch_address_name"):
		dispatch = so.get("dispatch_address_name") or get_company_dispatch_address(so.company)
		if dispatch:
			ps.dispatch_address_name = dispatch


def get_source_warehouse_for_pack_line(row) -> str | None:
	if row.get("dn_detail"):
		warehouse = frappe.db.get_value("Delivery Note Item", row.dn_detail, "warehouse")
		if warehouse:
			return warehouse

	from inventory_tools.inventory_tools.overrides.delivery_note_from_pack import resolve_so_detail
	from inventory_tools.inventory_tools.overrides.pack_stock_reservation import (
		resolve_warehouse_for_pack_reservation,
	)

	so_detail = resolve_so_detail(row)
	if row.get("against_sales_order") and so_detail:
		so_item = frappe.get_doc("Sales Order Item", so_detail)
		return resolve_warehouse_for_pack_reservation(row, so_item)
	return None


def ensure_alternative_sales_workflow_for_companies(companies: set[str]) -> None:
	for company in companies:
		if not company:
			continue
		if not frappe.db.exists("Inventory Tools Settings", company):
			frappe.throw(
				_(
					"Inventory Tools Settings is required for company {0} before enabling Shipstation Integration."
				).format(company)
			)
		settings = frappe.get_doc("Inventory Tools Settings", company)
		if not settings.enable_alternative_sales_workflow:
			settings.enable_alternative_sales_workflow = 1
			settings.save()


def companies_from_shipstation_settings(doc) -> set[str]:
	companies = {row.company for row in doc.get("shipstation_stores") or [] if row.company}
	if companies:
		return companies
	if doc.name and frappe.db.exists("Company", doc.name):
		return {doc.name}
	default_company = frappe.defaults.get_global_default("company")
	return {default_company} if default_company else set()


def companies_from_all_shipstation_settings() -> set[str]:
	companies: set[str] = set()
	for settings_name in frappe.get_all("Shipstation Settings", pluck="name"):
		doc = frappe.get_doc("Shipstation Settings", settings_name)
		companies.update(companies_from_shipstation_settings(doc))
	return companies


def ensure_alternative_sales_workflow_for_all_shipstation_settings() -> None:
	companies = companies_from_all_shipstation_settings()
	if companies:
		ensure_alternative_sales_workflow_for_companies(companies)
