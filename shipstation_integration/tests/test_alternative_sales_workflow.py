# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Alternative Sales Workflow through ShipStation class overrides."""

import json

import frappe
import pytest
from beam.tests.fixtures import customers
from frappe.contacts.doctype.contact.contact import get_default_contact
from frappe.utils import cint, flt, getdate

from erpnext.selling.doctype.sales_order.sales_order import create_pick_list
from erpnext.stock.doctype.pick_list.pick_list import create_stock_entry
from inventory_tools.inventory_tools.overrides.alternative_sales_workflow import (
	is_alternative_sales_workflow_enabled,
)
from inventory_tools.inventory_tools.overrides.delivery_note import (
	make_delivery_note_from_stock_entry,
)
from inventory_tools.inventory_tools.overrides.delivery_note_from_pack import (
	make_delivery_note_from_packing_slip,
	make_delivery_note_from_shipment,
)
from inventory_tools.inventory_tools.overrides.packing_slip import (
	make_packing_slip_from_sales_order,
	make_packing_slip_from_stock_entry,
	submit_delivery_note_from_packing_slip,
)
from inventory_tools.inventory_tools.overrides.shipment import (
	make_shipment_from_sales_order,
	make_shipment_from_stock_entry,
	submit_delivery_note_from_shipment,
)

from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
	ensure_alternative_sales_workflow_for_companies,
)

COMPANY = "Ambrosia Pie Company"
CUSTOMER = customers[1]
ITEM = "Ambrosia Pie"
SOURCE_WAREHOUSE = "Baked Goods - APC"
STAGING_WAREHOUSE = "Refrigerated Display - APC"
PIE_RATE = 10.2
CUSTOMER_SHIPPING_CITY = "Boston"


@pytest.fixture(scope="module", autouse=True)
def ensure_asw_enabled_for_shipstation():
	ensure_alternative_sales_workflow_for_companies({COMPANY})
	assert is_alternative_sales_workflow_enabled(COMPANY)


def save_mapped_doc(doc):
	doc.save()
	return doc.name


def customer_shipping_address(customer=CUSTOMER):
	return frappe.db.get_value(
		"Address",
		{"address_title": f"{customer} - {CUSTOMER_SHIPPING_CITY}"},
		"name",
	)


def make_test_sales_order(qty=5):
	address = customer_shipping_address()
	assert address, f"Missing shipping address for test customer {CUSTOMER}"
	so = frappe.new_doc("Sales Order")
	so.company = COMPANY
	so.customer = CUSTOMER
	so.customer_address = address
	so.shipping_address_name = address
	so.contact_person = get_default_contact("Customer", CUSTOMER)
	so.transaction_date = getdate()
	so.delivery_date = getdate()
	so.order_type = "Sales"
	so.currency = "USD"
	so.selling_price_list = "Bakery Wholesale"
	so.append(
		"items",
		{
			"item_code": ITEM,
			"qty": qty,
			"warehouse": SOURCE_WAREHOUSE,
			"delivery_date": getdate(),
		},
	)
	so.save()
	so.submit()
	return so


def make_submitted_material_transfer_stock_entry(so_name):
	pl = create_pick_list(so_name)
	pl.purpose = "Material Transfer"
	for loc in pl.locations:
		loc.picked_qty = loc.qty
	pl.save()
	pl.submit()

	stock_entry = frappe.get_doc(create_stock_entry(json.dumps(pl.as_dict())))
	stock_entry.to_warehouse = STAGING_WAREHOUSE
	for row in stock_entry.items:
		if row.s_warehouse and not row.t_warehouse:
			row.t_warehouse = STAGING_WAREHOUSE
	stock_entry.save()
	frappe.flags.beam_allow_source_rows_without_hu = True
	try:
		stock_entry.submit()
	finally:
		frappe.flags.beam_allow_source_rows_without_hu = False
	return pl, stock_entry


def assert_against_sales_order(rows, so):
	so_item = so.items[0].name
	for row in rows:
		assert row.against_sales_order == so.name
		assert row.so_detail == so_item


def assert_delivery_note_so_context(dn, so):
	assert dn.customer == so.customer
	assert dn.shipping_address_name == so.shipping_address_name
	assert_against_sales_order(dn.items, so)
	assert flt(dn.items[0].rate, 2) == flt(so.items[0].rate, 2)


def submit_packing_slip(ps_name):
	ps = frappe.get_doc("Packing Slip", ps_name)
	ps.from_case_no = 1
	ps.to_case_no = 1
	ps.save()
	ps.submit()
	return ps


def assert_delivery_note_issue(dn, qty, warehouse):
	sle = frappe.get_all(
		"Stock Ledger Entry",
		filters={
			"voucher_type": "Delivery Note",
			"voucher_no": dn.name,
			"item_code": ITEM,
			"is_cancelled": 0,
		},
		fields=["warehouse", "actual_qty", "valuation_rate"],
	)
	assert len(sle) == 1
	assert sle[0].warehouse == warehouse
	assert flt(sle[0].actual_qty) == flt(-qty)


def configure_stock_reservation(enabled: bool):
	previous = cint(frappe.db.get_single_value("Stock Settings", "enable_stock_reservation"))
	frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", 1 if enabled else 0)
	return previous


def restore_stock_reservation(previous):
	frappe.db.set_single_value("Stock Settings", "enable_stock_reservation", previous)


def configure_pack_reserve_modes(packing_slip_mode="Always", shipment_mode="Ask"):
	settings = frappe.get_doc("Inventory Tools Settings", COMPANY)
	previous = (
		settings.reserve_stock_on_packing_slip,
		settings.reserve_stock_on_shipment,
	)
	settings.reserve_stock_on_packing_slip = packing_slip_mode
	settings.reserve_stock_on_shipment = shipment_mode
	settings.save()
	return previous


def restore_pack_reserve_modes(previous):
	ps_mode, sh_mode = previous
	settings = frappe.get_doc("Inventory Tools Settings", COMPANY)
	settings.reserve_stock_on_packing_slip = ps_mode
	settings.reserve_stock_on_shipment = sh_mode
	settings.save()


def make_test_sales_order_with_reserve(qty=5):
	so = make_test_sales_order(qty)
	frappe.db.set_value("Sales Order Item", so.items[0].name, "reserve_stock", 1)
	so.reload()
	return so


def add_shipment_parcel(shipment):
	shipment.append(
		"shipment_parcel",
		{
			"length": 5,
			"width": 5,
			"height": 5,
			"weight": 5,
			"count": 1,
			"length_uom": "Inch",
			"weight_uom": "Pound",
		},
	)
	shipment.save()
	return shipment


def submit_shipment(shipment_name):
	shipment = frappe.get_doc("Shipment", shipment_name)
	if not shipment.shipment_parcel:
		add_shipment_parcel(shipment)
	shipment.submit()
	return shipment


def get_pack_sres(pack_doctype, pack_name):
	return frappe.get_all(
		"Stock Reservation Entry",
		filters={
			"pack_from_doctype": pack_doctype,
			"pack_from_name": pack_name,
			"docstatus": 1,
		},
		pluck="name",
	)


@pytest.mark.order(130)
def test_shipstation_requires_alternative_sales_workflow():
	assert is_alternative_sales_workflow_enabled(COMPANY)


@pytest.mark.order(131)
def test_sales_order_to_packing_slip_without_delivery_note():
	so = make_test_sales_order(qty=3)

	ps_name = save_mapped_doc(make_packing_slip_from_sales_order(so.name))
	ps = frappe.get_doc("Packing Slip", ps_name)

	assert ps.docstatus == 0
	assert not ps.delivery_note
	assert ps.shipping_address_name
	assert ps.dispatch_address_name
	assert_against_sales_order(ps.items, so)


@pytest.mark.order(132)
def test_sales_order_to_shipment_without_delivery_note():
	so = make_test_sales_order(qty=4)

	shipment_name = save_mapped_doc(make_shipment_from_sales_order(so.name))
	shipment = frappe.get_doc("Shipment", shipment_name)

	assert shipment.docstatus == 0
	assert shipment.delivery_address_name
	assert shipment.pickup_address_name
	assert shipment.delivery_contact_name
	assert not shipment.delivery_note
	assert len(shipment.shipment_delivery_note) >= 1
	assert all(not row.delivery_note for row in shipment.shipment_delivery_note)
	assert_against_sales_order(shipment.shipment_delivery_note, so)


@pytest.mark.order(133)
def test_stock_entry_to_delivery_note_uses_staging_warehouse():
	so = make_test_sales_order(qty=2)
	pl, se = make_submitted_material_transfer_stock_entry(so.name)

	dn_name = make_delivery_note_from_stock_entry(se.name)
	dn = frappe.get_doc("Delivery Note", dn_name)

	assert dn.docstatus == 0
	assert all(row.warehouse == STAGING_WAREHOUSE for row in dn.items)
	assert_against_sales_order(se.items, so)
	assert_against_sales_order(dn.items, so)


@pytest.mark.order(134)
def test_stock_entry_to_packing_slip_without_delivery_note():
	so = make_test_sales_order(qty=2)
	pl, se = make_submitted_material_transfer_stock_entry(so.name)

	ps_name = save_mapped_doc(make_packing_slip_from_stock_entry(se.name))
	ps = frappe.get_doc("Packing Slip", ps_name)

	assert ps.docstatus == 0
	assert not ps.delivery_note
	assert ps.shipping_address_name
	assert all(row.t_warehouse == STAGING_WAREHOUSE for row in se.items)
	assert_against_sales_order(se.items, so)
	assert_against_sales_order(ps.items, so)


@pytest.mark.order(135)
def test_stock_entry_to_shipment_without_delivery_note():
	so = make_test_sales_order(qty=2)
	pl, se = make_submitted_material_transfer_stock_entry(so.name)

	shipment_name = save_mapped_doc(make_shipment_from_stock_entry(se.name))
	shipment = frappe.get_doc("Shipment", shipment_name)

	assert shipment.docstatus == 0
	assert shipment.delivery_address_name
	assert all(not row.delivery_note for row in shipment.shipment_delivery_note)
	assert_against_sales_order(se.items, so)
	assert_against_sales_order(shipment.shipment_delivery_note, so)


@pytest.mark.order(136)
def test_packing_slip_delivery_note_mapper_includes_sales_order_context():
	so = make_test_sales_order(qty=2)
	ps_name = save_mapped_doc(make_packing_slip_from_sales_order(so.name))
	submit_packing_slip(ps_name)

	dn = make_delivery_note_from_packing_slip(ps_name)

	assert dn.docstatus == 0
	assert_delivery_note_so_context(dn, so)
	assert flt(dn.items[0].qty) == 2
	assert dn.items[0].warehouse == SOURCE_WAREHOUSE


@pytest.mark.order(137)
def test_packing_slip_submits_linked_delivery_note():
	so = make_test_sales_order(qty=2)
	ps_name = save_mapped_doc(make_packing_slip_from_sales_order(so.name))
	submit_packing_slip(ps_name)

	dn_name = submit_delivery_note_from_packing_slip(ps_name)
	dn = frappe.get_doc("Delivery Note", dn_name)

	assert dn.docstatus == 1
	assert_against_sales_order(dn.items, so)
	assert_delivery_note_so_context(dn, so)
	assert_delivery_note_issue(dn, qty=2, warehouse=SOURCE_WAREHOUSE)


@pytest.mark.order(138)
def test_shipment_delivery_note_mapper_includes_sales_order_context():
	so = make_test_sales_order(qty=2)
	shipment_name = save_mapped_doc(make_shipment_from_sales_order(so.name))

	dn = make_delivery_note_from_shipment(shipment_name)

	assert dn.docstatus == 0
	assert_delivery_note_so_context(dn, so)
	assert flt(dn.items[0].qty) == 2
	assert dn.items[0].warehouse == SOURCE_WAREHOUSE


@pytest.mark.order(139)
def test_shipment_submits_linked_delivery_note():
	so = make_test_sales_order(qty=2)
	shipment_name = save_mapped_doc(make_shipment_from_sales_order(so.name))
	submit_shipment(shipment_name)

	dn_name = submit_delivery_note_from_shipment(shipment_name)
	dn = frappe.get_doc("Delivery Note", dn_name)
	shipment = frappe.get_doc("Shipment", shipment_name)

	assert dn.docstatus == 1
	assert shipment.delivery_note == dn_name
	assert all(row.delivery_note == dn_name for row in shipment.shipment_delivery_note)
	assert_against_sales_order(shipment.shipment_delivery_note, so)
	assert_delivery_note_so_context(dn, so)
	assert_delivery_note_issue(dn, qty=2, warehouse=SOURCE_WAREHOUSE)


@pytest.mark.order(140)
def test_packing_slip_always_creates_stock_reservation_on_submit():
	previous_reservation = configure_stock_reservation(True)
	previous_modes = configure_pack_reserve_modes("Always", "Ask")
	try:
		so = make_test_sales_order_with_reserve(qty=2)
		ps_name = save_mapped_doc(make_packing_slip_from_sales_order(so.name))
		submit_packing_slip(ps_name)

		sres = get_pack_sres("Packing Slip", ps_name)
		assert len(sres) == 1
		assert frappe.db.get_value("Stock Reservation Entry", sres[0], "warehouse") == SOURCE_WAREHOUSE
	finally:
		restore_pack_reserve_modes(previous_modes)
		restore_stock_reservation(previous_reservation)


@pytest.mark.order(141)
def test_shipment_ask_quote_path_submits_without_reservation():
	previous_reservation = configure_stock_reservation(True)
	previous_modes = configure_pack_reserve_modes("Always", "Ask")
	try:
		from inventory_tools.inventory_tools.overrides.pack_stock_reservation import (
			shipment_needs_stock_reservation,
		)

		so = make_test_sales_order_with_reserve(qty=2)
		shipment_name = save_mapped_doc(make_shipment_from_sales_order(so.name))
		assert shipment_needs_stock_reservation(shipment_name)
		submit_shipment(shipment_name)

		assert not get_pack_sres("Shipment", shipment_name)
	finally:
		restore_pack_reserve_modes(previous_modes)
		restore_stock_reservation(previous_reservation)


@pytest.mark.order(142)
def test_packing_slip_cancel_cancels_pack_stock_reservation():
	previous_reservation = configure_stock_reservation(True)
	previous_modes = configure_pack_reserve_modes("Always", "Ask")
	try:
		so = make_test_sales_order_with_reserve(qty=2)
		ps_name = save_mapped_doc(make_packing_slip_from_sales_order(so.name))
		submit_packing_slip(ps_name)
		assert len(get_pack_sres("Packing Slip", ps_name)) == 1

		ps = frappe.get_doc("Packing Slip", ps_name)
		ps.cancel()
		assert not get_pack_sres("Packing Slip", ps_name)
	finally:
		restore_pack_reserve_modes(previous_modes)
		restore_stock_reservation(previous_reservation)


@pytest.mark.order(143)
def test_ltl_weight_from_so_detail_without_dn_detail():
	from shipstation_integration.ltl import ShipstationLTL
	from shipstation_integration.tests.setup import ensure_exterior_physical_dimensions_for_pie_items

	ensure_exterior_physical_dimensions_for_pie_items(commit=True, item_codes=[ITEM])

	so = make_test_sales_order(qty=2)
	shipment_name = save_mapped_doc(make_shipment_from_sales_order(so.name))
	shipment = frappe.get_doc("Shipment", shipment_name)
	row = shipment.shipment_delivery_note[0]
	assert not row.delivery_note
	assert row.so_detail

	ltl = ShipstationLTL()
	weight, uom = ltl.sdn_row_weight_from_items(row)
	assert weight > 0
	assert uom


@pytest.mark.order(144)
def test_cartonize_so_mapped_packing_slip():
	from shipstation_integration.cartonization import (
		apply_cartonization_to_packing_slip,
		stable_child_row_key_for_cartonization,
	)
	from shipstation_integration.tests.setup import ensure_exterior_physical_dimensions_for_pie_items

	ensure_exterior_physical_dimensions_for_pie_items(commit=True, item_codes=[ITEM])
	settings = frappe.get_doc("Shipstation Settings", COMPANY)
	settings.enable_cartonization = 1
	settings.auto_cartonize_packing_slip = 0
	settings.save()

	so = make_test_sales_order(qty=2)
	ps_name = save_mapped_doc(make_packing_slip_from_sales_order(so.name))
	ps = frappe.get_doc("Packing Slip", ps_name)
	assert stable_child_row_key_for_cartonization(ps.items[0]) == ps.items[0].so_detail

	apply_cartonization_to_packing_slip(ps_name)
	ps.reload()
	assert any(item.parcel_number for item in ps.items)


@pytest.mark.order(145)
def test_company_resolver_without_delivery_note():
	from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
		get_company_from_packing_slip,
		get_customer_from_packing_slip,
	)

	so = make_test_sales_order(qty=2)
	ps_name = save_mapped_doc(make_packing_slip_from_sales_order(so.name))
	ps = frappe.get_doc("Packing Slip", ps_name)

	assert get_company_from_packing_slip(ps) == COMPANY
	customer, customer_name = get_customer_from_packing_slip(ps)
	assert customer == CUSTOMER
	assert customer_name
