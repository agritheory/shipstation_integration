# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from beam.beam.handling_unit import generate_handling_units
from frappe.utils import flt, today

from shipstation_integration.shipstation_integration.overrides.handling_unit import (
	create_handling_unit_for_sscc,
)

SSCC_CREATE_TEST = "106141410099900011"
SSCC_REPACK_MAIN = "106141410099900035"
SSCC_PS_BACKLINK = "106141410099900059"
SSCC_NO_HU_DN = "106141410099900073"


def get_test_customer() -> str:
	return frappe.get_value("Customer", {"customer_name": "Almacs Food Group"}) or "Almacs Food Group"


def ensure_stock(item_code: str, qty: float, warehouse: str = "Baked Goods - APC") -> str | None:
	rate = (
		frappe.get_value(
			"Item Price",
			{"item_code": item_code, "price_list": "Bakery Wholesale"},
			"price_list_rate",
		)
		or 10.0
	)
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = se.purpose = "Material Receipt"
	se.company = frappe.defaults.get_defaults().get("company")
	se.append(
		"items",
		{
			"item_code": item_code,
			"qty": qty,
			"t_warehouse": warehouse,
			"basic_rate": rate,
		},
	)
	se.save()
	generate_handling_units(se, None)
	se.submit()
	se.reload()
	hu = getattr(se.items[0], "handling_unit", None) if se.items else None
	if not hu and se.items:
		hu = frappe.db.get_value(
			"Stock Entry Detail",
			{"parent": se.name, "idx": se.items[0].idx},
			"handling_unit",
		)
	return hu


def ensure_stock_without_hu(
	item_code: str, qty: float, warehouse: str = "Baked Goods - APC"
) -> None:
	company = frappe.defaults.get_defaults().get("company")
	beam_settings = frappe.get_doc("BEAM Settings", {"company": company})
	original = beam_settings.enable_handling_units

	beam_settings.enable_handling_units = 0
	beam_settings.save()

	try:
		rate = (
			frappe.get_value(
				"Item Price",
				{"item_code": item_code, "price_list": "Bakery Wholesale"},
				"price_list_rate",
			)
			or 10.0
		)
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = se.purpose = "Material Receipt"
		se.company = company
		se.append(
			"items",
			{
				"item_code": item_code,
				"qty": qty,
				"t_warehouse": warehouse,
				"basic_rate": rate,
			},
		)
		se.save()
		se.submit()
		hu = getattr(se.items[0], "handling_unit", None)
		assert not hu, "Test setup error: stock SE item should have no handling_unit"
	finally:
		beam_settings.enable_handling_units = original
		beam_settings.save()


def create_so(customer: str, item_code: str, qty: int, warehouse: str = "Baked Goods - APC"):
	so = frappe.new_doc("Sales Order")
	so.customer = customer
	so.transaction_date = today()
	so.company = frappe.defaults.get_defaults().get("company")
	so.selling_price_list = "Bakery Wholesale"
	so.append(
		"items",
		{
			"item_code": item_code,
			"qty": qty,
			"delivery_date": today(),
			"warehouse": warehouse,
		},
	)
	so.save()
	so.submit()
	return so


def build_ps_with_ssccs(dn_doc, sscc_by_item_code: dict):
	ps = make_packing_slip(dn_doc.name)
	parcel_counter = 1
	for item in ps.items:
		sscc = sscc_by_item_code.get(item.item_code)
		if sscc:
			item.parcel_number = parcel_counter
			item.ucc128 = sscc
			parcel_counter += 1
	ps.save()
	return ps


@pytest.mark.order(100)
def test_handling_unit_created_on_sscc_insert():
	if frappe.db.exists("Handling Unit", SSCC_CREATE_TEST):
		frappe.delete_doc("Handling Unit", SSCC_CREATE_TEST, force=True)

	result = create_handling_unit_for_sscc(SSCC_CREATE_TEST)

	assert result == SSCC_CREATE_TEST

	hu = frappe.get_doc("Handling Unit", SSCC_CREATE_TEST)
	assert hu.name == SSCC_CREATE_TEST
	assert hu.handling_unit_name == SSCC_CREATE_TEST


@pytest.mark.order(101)
def test_packing_slip_links_handling_units():
	ensure_stock("Ambrosia Pie", 50)

	so = create_so(get_test_customer(), "Ambrosia Pie", 5)
	dn = make_delivery_note(so.name)
	dn.save()

	ps = build_ps_with_ssccs(dn, {"Ambrosia Pie": SSCC_REPACK_MAIN})

	assert ps.items[0].ucc128 == SSCC_REPACK_MAIN
	assert ps.items[0].parcel_number

	ps.submit()
	ps.reload()

	se_name = ps.get("repack_stock_entry")
	assert se_name

	se = frappe.get_doc("Stock Entry", se_name)
	assert se.docstatus == 1
	assert se.stock_entry_type == "Repack"

	target_rows = [r for r in se.items if r.t_warehouse]
	assert target_rows
	for row in target_rows:
		assert row.handling_unit == SSCC_REPACK_MAIN


@pytest.mark.order(102)
def test_sscc_handling_unit_doc_created_on_ps_submit():
	ensure_stock("Ambrosia Pie", 30)

	if frappe.db.exists("Handling Unit", SSCC_PS_BACKLINK):
		frappe.delete_doc("Handling Unit", SSCC_PS_BACKLINK, force=True)

	so = create_so(get_test_customer(), "Ambrosia Pie", 5)
	dn = make_delivery_note(so.name)
	dn.save()

	ps = build_ps_with_ssccs(dn, {"Ambrosia Pie": SSCC_PS_BACKLINK})
	ps.submit()

	assert frappe.db.exists("Handling Unit", SSCC_PS_BACKLINK)
	hu = frappe.get_doc("Handling Unit", SSCC_PS_BACKLINK)
	assert hu.name == SSCC_PS_BACKLINK


@pytest.mark.order(103)
def test_delivery_note_hu_sync_repack_without_inbound_hu():
	ensure_stock_without_hu("Kaduka Key Lime Pie", 30)

	if frappe.db.exists("Handling Unit", SSCC_NO_HU_DN):
		frappe.delete_doc("Handling Unit", SSCC_NO_HU_DN, force=True)

	so = create_so(get_test_customer(), "Kaduka Key Lime Pie", 10)
	dn = make_delivery_note(so.name)
	dn.save()

	dn_hu = frappe.db.get_value("Delivery Note Item", dn.items[0].name, "handling_unit")
	assert not dn_hu

	ps = build_ps_with_ssccs(dn, {"Kaduka Key Lime Pie": SSCC_NO_HU_DN})
	ps.submit()
	ps.reload()

	se_name = ps.get("repack_stock_entry")
	assert se_name

	se = frappe.get_doc("Stock Entry", se_name)
	assert se.docstatus == 1
	assert se.stock_entry_type == "Repack"

	source_rows = [r for r in se.items if r.s_warehouse]
	assert source_rows
	for row in source_rows:
		assert not row.handling_unit

	target_rows = [r for r in se.items if r.t_warehouse]
	assert target_rows
	for row in target_rows:
		assert row.handling_unit == SSCC_NO_HU_DN

	assert flt(se.total_outgoing_value) == flt(se.total_incoming_value)
	assert not frappe.flags.get("beam_allow_source_rows_without_hu")
