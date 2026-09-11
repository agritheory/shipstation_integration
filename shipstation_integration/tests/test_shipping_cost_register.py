# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from frappe.utils import add_days, flt, today

from shipstation_integration.shipstation_integration.report.shipping_cost_register.shipping_cost_register import (
	execute as execute_shipping_cost_register,
)
from shipstation_integration.tests.setup import get_draft_ltl_shipment_for_tests


COMPANY = "Ambrosia Pie Company"
PARCEL_TRACKING = "9400111899223100008888"
SHIPMENT_PRO = "PRO-SCR-0001"
PARCEL_FREIGHT = 18.75
LTL_FREIGHT = 474.38


def report_filters(**extra):
	filters = frappe._dict(
		{
			"from_date": add_days(today(), -400),
			"to_date": add_days(today(), 30),
			"company": COMPANY,
			"shipment_type": "All",
		}
	)
	filters.update(extra)
	return filters


def run_register(**extra):
	columns, rows = execute_shipping_cost_register(report_filters(**extra))
	assert columns
	return rows


def seed_packing_slip():
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()
	return ps


def row_for_document(rows, document):
	return next((row for row in rows if row.get("document") == document), None)


def set_packing_slip_tracking(ps, tracking_number):
	row = ps.items[0]
	frappe.db.set_value("Packing Slip Item", row.name, "tracking_number", tracking_number)
	ps.reload()


def add_shipping_tax(delivery_note, amount):
	dn = frappe.get_doc("Delivery Note", delivery_note)
	account = frappe.db.get_value(
		"Account",
		{"account_name": "Freight and Forwarding Charges", "company": dn.company},
		"name",
	) or frappe.db.get_value("Account", {"company": dn.company, "is_group": 0}, "name")
	dn.append(
		"taxes",
		{
			"charge_type": "Actual",
			"description": "Shipping",
			"tax_amount": amount,
			"account_head": account,
		},
	)
	dn.save()
	return dn.taxes[-1].name


def remove_shipping_tax(delivery_note, tax_row):
	dn = frappe.get_doc("Delivery Note", delivery_note)
	dn.taxes = [row for row in dn.taxes if row.name != tax_row]
	dn.save()


@pytest.mark.order(86)
def test_untracked_packing_slip_and_unbooked_shipment_are_excluded():
	ps = seed_packing_slip()
	shipment = get_draft_ltl_shipment_for_tests()
	assert not any(row.tracking_number for row in ps.items)

	rows = run_register()
	assert row_for_document(rows, ps.name) is None
	assert row_for_document(rows, shipment.name) is None


@pytest.mark.order(87)
def test_parcel_row_from_packing_slip_with_tracking_and_dn_freight():
	ps = seed_packing_slip()
	original_po = frappe.db.get_value("Delivery Note", ps.delivery_note, "po_no")
	original_channel = frappe.db.get_value("Delivery Note", ps.delivery_note, "marketplace")
	tax_row = None
	try:
		set_packing_slip_tracking(ps, PARCEL_TRACKING)
		frappe.db.set_value("Delivery Note", ps.delivery_note, "po_no", "PO-SCR-77")
		frappe.db.set_value("Delivery Note", ps.delivery_note, "marketplace", "Amazon")
		tax_row = add_shipping_tax(ps.delivery_note, PARCEL_FREIGHT)

		row = row_for_document(run_register(), ps.name)
		assert row is not None
		assert row.shipment_type == "Parcel"
		assert row.document_type == "Packing Slip"
		assert row.delivery_note == ps.delivery_note
		assert row.customer == "Beans and Dreams Roasters"
		assert row.sales_channel == "Amazon"
		assert row.customer_po == "PO-SCR-77"
		assert row.carrier == "USPS"
		assert row.service == "usps_priority_mail"
		assert row.containers >= 1
		assert PARCEL_TRACKING in row.tracking
		assert flt(row.freight_cost, 2) == flt(PARCEL_FREIGHT, 2)
		assert row.sales_order
	finally:
		set_packing_slip_tracking(ps, None)
		frappe.db.set_value("Delivery Note", ps.delivery_note, "po_no", original_po)
		frappe.db.set_value("Delivery Note", ps.delivery_note, "marketplace", original_channel)
		if tax_row:
			remove_shipping_tax(ps.delivery_note, tax_row)


@pytest.mark.order(88)
def test_shipment_row_from_booked_ltl():
	shipment = get_draft_ltl_shipment_for_tests()
	original = {
		"awb_number": shipment.awb_number,
		"shipment_amount": shipment.shipment_amount,
		"carrier": shipment.carrier,
		"carrier_service": shipment.carrier_service,
	}
	try:
		frappe.db.set_value(
			"Shipment",
			shipment.name,
			{
				"awb_number": SHIPMENT_PRO,
				"shipment_amount": LTL_FREIGHT,
				"carrier": "ODFL",
				"carrier_service": "stnd",
			},
		)

		row = row_for_document(run_register(), shipment.name)
		assert row is not None
		assert row.shipment_type == "LTL"
		assert row.document_type == "Shipment"
		assert row.customer == "Almacs Food Group"
		assert row.carrier == "ODFL"
		assert row.service == "stnd"
		assert row.tracking == SHIPMENT_PRO
		assert row.freight_term == "Prepaid"
		assert row.containers >= 1
		assert flt(row.freight_cost, 2) == flt(LTL_FREIGHT, 2)
		assert row.delivery_note
	finally:
		frappe.db.set_value("Shipment", shipment.name, original)


@pytest.mark.order(89)
def test_type_filter_parcel_excludes_ltl_shipment():
	ps = seed_packing_slip()
	shipment = get_draft_ltl_shipment_for_tests()
	try:
		set_packing_slip_tracking(ps, PARCEL_TRACKING)
		frappe.db.set_value("Shipment", shipment.name, "awb_number", SHIPMENT_PRO)

		parcel_rows = run_register(shipment_type="Parcel")
		assert row_for_document(parcel_rows, ps.name) is not None
		assert row_for_document(parcel_rows, shipment.name) is None

		ltl_rows = run_register(shipment_type="LTL")
		assert row_for_document(ltl_rows, ps.name) is None
		assert row_for_document(ltl_rows, shipment.name) is not None
	finally:
		set_packing_slip_tracking(ps, None)
		frappe.db.set_value("Shipment", shipment.name, "awb_number", None)
