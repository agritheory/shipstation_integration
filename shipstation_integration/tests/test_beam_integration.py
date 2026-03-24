# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from beam.beam.handling_unit import generate_handling_units
from frappe.utils import flt, today

from shipstation_integration.beam_integration import create_handling_unit_for_sscc

# Pre-computed 18-digit strings using the test GS1 company prefix "0614141"
# (set in Shipstation Settings by create_shipstation_settings() in setup.py).
# Each constant is used by exactly one test to avoid cross-test HU collisions.

SSCC_CREATE_TEST = "106141410099900011"
SSCC_IDEMPOTENT_TEST = "106141410099900028"
SSCC_REPACK_MAIN = "106141410099900035"
SSCC_REPACK_VALUATION = "106141410099900042"
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
		assert (
			not hu
		), "Test setup error: stock SE item should have no handling_unit when enable_handling_units=0"
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


def test_create_handling_unit_for_sscc():
	if frappe.db.exists("Handling Unit", SSCC_CREATE_TEST):
		frappe.delete_doc("Handling Unit", SSCC_CREATE_TEST, force=True)

	result = create_handling_unit_for_sscc(SSCC_CREATE_TEST)

	assert result == SSCC_CREATE_TEST

	hu = frappe.get_doc("Handling Unit", SSCC_CREATE_TEST)
	assert hu.name == SSCC_CREATE_TEST
	assert hu.handling_unit_name == SSCC_CREATE_TEST


def test_create_handling_unit_for_sscc_idempotent():
	if frappe.db.exists("Handling Unit", SSCC_IDEMPOTENT_TEST):
		frappe.delete_doc("Handling Unit", SSCC_IDEMPOTENT_TEST, force=True)

	r1 = create_handling_unit_for_sscc(SSCC_IDEMPOTENT_TEST)
	r2 = create_handling_unit_for_sscc(SSCC_IDEMPOTENT_TEST)  # HU already exists

	assert r1 == SSCC_IDEMPOTENT_TEST
	assert r2 == SSCC_IDEMPOTENT_TEST


@pytest.mark.order(20)
def test_repack_se_target_rows_use_sscc_as_handling_unit():
	ensure_stock("Ambrosia Pie", 50)

	so = create_so(get_test_customer(), "Ambrosia Pie", 5)
	dn = make_delivery_note(so.name)
	dn.save()

	ps = build_ps_with_ssccs(dn, {"Ambrosia Pie": SSCC_REPACK_MAIN})

	# Preconditions — verify test setup is correct before submitting.
	assert ps.items[0].ucc128 == SSCC_REPACK_MAIN, "SSCC not set on PS item — test setup error"
	assert ps.items[0].parcel_number, "parcel_number not set on PS item — test setup error"

	ps.submit()
	ps.reload()

	se_name = ps.get("repack_stock_entry")
	assert se_name, "Repack Stock Entry was not linked back to Packing Slip.repack_stock_entry"

	se = frappe.get_doc("Stock Entry", se_name)
	assert se.docstatus == 1, "Repack SE was not submitted"
	assert se.stock_entry_type == "Repack"

	target_rows = [r for r in se.items if r.t_warehouse]
	assert target_rows, "No target rows found in the Repack SE"

	for row in target_rows:
		assert row.handling_unit == SSCC_REPACK_MAIN, (
			f"Target row handling_unit '{row.handling_unit}' != SSCC '{SSCC_REPACK_MAIN}'. "
			"BEAM may have overwritten the pre-set SSCC with a UUID-derived value."
		)
		# Note: ERPNext's StockEntry.validate() sets is_finished_item=1 on all
		# target rows of a Repack SE.  BEAM's generate_handling_units hook checks
		# row.get("handling_unit") FIRST and skips any row that already has one —
		# so a pre-set SSCC survives regardless of is_finished_item.  We do not
		# assert on is_finished_item here because it is legitimately 1.


@pytest.mark.order(21)
def test_repack_se_valuation_is_balanced():
	ensure_stock("Gooseberry Pie", 30)

	so = create_so(get_test_customer(), "Gooseberry Pie", 10)
	dn = make_delivery_note(so.name)
	dn.save()

	ps = build_ps_with_ssccs(dn, {"Gooseberry Pie": SSCC_REPACK_VALUATION})
	ps.submit()
	ps.reload()

	se_name = ps.get("repack_stock_entry")
	assert se_name, "Repack SE not linked to Packing Slip"

	se = frappe.get_doc("Stock Entry", se_name)

	assert (
		flt(se.total_outgoing_value) > 0
	), "Source rows have zero total_outgoing_value — valuation rate lookup failed"
	assert flt(se.total_outgoing_value) == flt(se.total_incoming_value), (
		f"Repack SE is not cost-balanced: "
		f"outgoing={se.total_outgoing_value}, incoming={se.total_incoming_value}"
	)

	source_rows = [r for r in se.items if r.s_warehouse]
	for row in source_rows:
		assert flt(row.basic_rate) > 0, (
			f"Source row for {row.item_code} has zero basic_rate — "
			"get_incoming_rate did not return a value"
		)


@pytest.mark.order(22)
def test_sscc_handling_unit_doc_created_on_ps_submit():
	ensure_stock("Ambrosia Pie", 30)

	if frappe.db.exists("Handling Unit", SSCC_PS_BACKLINK):
		frappe.delete_doc("Handling Unit", SSCC_PS_BACKLINK, force=True)

	so = create_so(get_test_customer(), "Ambrosia Pie", 5)
	dn = make_delivery_note(so.name)
	dn.save()

	ps = build_ps_with_ssccs(dn, {"Ambrosia Pie": SSCC_PS_BACKLINK})
	ps.submit()

	assert frappe.db.exists(
		"Handling Unit", SSCC_PS_BACKLINK
	), f"Handling Unit '{SSCC_PS_BACKLINK}' was not created after Packing Slip submit"
	hu = frappe.get_doc("Handling Unit", SSCC_PS_BACKLINK)
	assert hu.name == SSCC_PS_BACKLINK


@pytest.mark.order(23)
def test_repack_se_name_written_back_to_packing_slip():
	ensure_stock("Gooseberry Pie", 20)

	sscc = "106141410099900066"
	if frappe.db.exists("Handling Unit", sscc):
		frappe.delete_doc("Handling Unit", sscc, force=True)

	so = create_so(get_test_customer(), "Gooseberry Pie", 5)
	dn = make_delivery_note(so.name)
	dn.save()

	ps = build_ps_with_ssccs(dn, {"Gooseberry Pie": sscc})
	ps.submit()
	ps.reload()

	se_name = ps.get("repack_stock_entry")
	assert se_name, "repack_stock_entry field was not set on Packing Slip after submit"

	se = frappe.get_doc("Stock Entry", se_name)
	assert se.docstatus == 1


@pytest.mark.order(24)
def test_packing_slip_without_ssccs_skips_repack():
	ensure_stock("Ambrosia Pie", 20)

	so = create_so(get_test_customer(), "Ambrosia Pie", 3)
	dn = make_delivery_note(so.name)
	dn.save()

	ps = make_packing_slip(dn.name)
	# Assign parcel numbers (required by before_submit) but leave ucc128 empty.
	for idx, item in enumerate(ps.items, start=1):
		item.parcel_number = idx
	ps.save()
	ps.submit()
	ps.reload()

	assert not ps.get(
		"repack_stock_entry"
	), "repack_stock_entry should be empty when PS items have no SSCC codes"


@pytest.mark.order(25)
def test_repack_se_dn_without_handling_units():
	# Step 1: create stock without HU tracking — the normal receipt flow for
	# customers who only use HUs on the packing slip.
	ensure_stock_without_hu("Kaduka Key Lime Pie", 30)

	if frappe.db.exists("Handling Unit", SSCC_NO_HU_DN):
		frappe.delete_doc("Handling Unit", SSCC_NO_HU_DN, force=True)

	# Step 2: create SO → DN.  The DN item has no handling_unit — expected for
	# customers whose inbound movements are not HU-tracked.
	so = create_so(get_test_customer(), "Kaduka Key Lime Pie", 10)
	dn = make_delivery_note(so.name)
	dn.save()

	dn_hu = frappe.db.get_value("Delivery Note Item", dn.items[0].name, "handling_unit")
	assert not dn_hu, (
		f"Test setup error: DN item should have handling_unit=None for "
		f"packing-slip-only HU workflow, got '{dn_hu}'"
	)

	# Step 3: build PS with SSCC and submit.
	ps = build_ps_with_ssccs(dn, {"Kaduka Key Lime Pie": SSCC_NO_HU_DN})
	ps.submit()
	ps.reload()

	# Step 4: verify Repack SE was created and submitted.
	se_name = ps.get("repack_stock_entry")
	assert se_name, (
		"Repack SE not linked to Packing Slip — create_packing_slip_repack_entry may "
		"have returned None or the back-link write failed"
	)

	se = frappe.get_doc("Stock Entry", se_name)
	assert se.docstatus == 1, (
		"Repack SE was not submitted — BEAM's validate_items_with_handling_unit "
		"may not have been bypassed by frappe.flags.beam_allow_source_rows_without_hu"
	)
	assert se.stock_entry_type == "Repack"

	# Source rows must have no handling_unit — the expected state when only
	# the packing slip step uses HUs.
	source_rows = [r for r in se.items if r.s_warehouse]
	assert source_rows, "No source rows found in Repack SE"
	for row in source_rows:
		assert not row.handling_unit, (
			f"Source row for {row.item_code} should have handling_unit=None "
			f"(packing-slip-only HU workflow), got '{row.handling_unit}'"
		)

	# Target rows must carry the SSCC.
	target_rows = [r for r in se.items if r.t_warehouse]
	assert target_rows, "No target rows found in Repack SE"
	for row in target_rows:
		assert (
			row.handling_unit == SSCC_NO_HU_DN
		), f"Target row handling_unit '{row.handling_unit}' != SSCC '{SSCC_NO_HU_DN}'"

	assert flt(se.total_outgoing_value) == flt(se.total_incoming_value), (
		f"Repack SE not cost-balanced for no-HU source: "
		f"outgoing={se.total_outgoing_value}, incoming={se.total_incoming_value}"
	)

	# The bypass flag must have been reset by the finally block.
	assert not frappe.flags.get(
		"beam_allow_source_rows_without_hu"
	), "frappe.flags.beam_allow_source_rows_without_hu was not reset after SE submit"
