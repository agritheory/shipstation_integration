# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Tests for the BEAM handling unit integration (beam_integration.py).

These tests verify that:
  1. ``create_handling_unit_for_sscc`` creates a Handling Unit whose *name*
     is the SSCC-18 string so BEAM's barcode scan dispatcher resolves a
     scanned GS1-128 label directly to the HU.

  2. On Packing Slip submit, ``create_packing_slip_repack_entry`` produces a
     submitted Repack Stock Entry where:
       - Every target row's ``handling_unit`` equals the SSCC code from the
         Packing Slip Item — NOT a BEAM UUID-derived value.
       - ``is_finished_item`` is False on target rows that received a pre-set
         SSCC HU (BEAM's ``generate_handling_units`` hook must skip them).
       - Source rows carry a non-zero ``basic_rate`` derived from the SLE
         history via ``get_incoming_rate``.
       - The SE is cost-balanced (total_outgoing_value == total_incoming_value).
       - The SE name is written back to ``Packing Slip.repack_stock_entry``.

  3. The packing-slip-only HU workflow succeeds when Delivery Note items have
     no ``handling_unit`` — the expected state for customers who use BEAM HUs
     exclusively for outbound packing (SSCC labels) and do not HU-track their
     receipts, transfers, or picks:
       - BEAM's ``validate_items_with_handling_unit`` hook is bypassed via
         ``frappe.flags.beam_allow_source_rows_without_hu``.
       - Source rows in the Repack SE have ``handling_unit = None``.
       - Target rows still receive the SSCC codes.
       - The flag is reset to False after submit (no flag leak).

Regression context
------------------
Before the fix two compounding bugs caused BEAM UUID HUs to appear instead of
SSCC codes on target rows:

  Bug A — The SSCC tracking branch was gated on ``has_hu_on_psi`` (whether
  Packing Slip Item has BEAM's inventory dimension field).  In environments
  where that dimension is not active on PSI, every target row fell through to
  the ``else: row.is_finished_item = 1`` branch and BEAM auto-generated UUIDs.

  Bug B — The code tried to back-fill the HU via ``frappe.db.set_value`` after
  the first ``se.save()``.  But ``se.submit()`` immediately calls ``se.save()``
  again from the *in-memory* doc object, overwriting the DB value with
  ``handling_unit = None`` before BEAM's ``generate_handling_units`` hook ran.
  The fix: set ``row.handling_unit = sscc`` on the **in-memory row** so the
  value survives the submit-time save and BEAM's hook finds it already set.
"""

import frappe
import pytest
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from frappe.utils import flt, today

from shipstation_integration.beam_integration import create_handling_unit_for_sscc

# ---------------------------------------------------------------------------
# Test SSCC constants
# ---------------------------------------------------------------------------
# Pre-computed 18-digit strings using the test GS1 company prefix "0614141"
# (set in Shipstation Settings by create_shipstation_settings() in setup.py).
# Each constant is used by exactly one test to avoid cross-test HU collisions.

SSCC_CREATE_TEST = "106141410099900011"
SSCC_IDEMPOTENT_TEST = "106141410099900028"
SSCC_REPACK_MAIN = "106141410099900035"
SSCC_REPACK_VALUATION = "106141410099900042"
SSCC_PS_BACKLINK = "106141410099900059"
SSCC_NO_HU_DN = "106141410099900073"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def get_test_customer() -> str:
	return frappe.get_value("Customer", {"customer_name": "Almacs Food Group"}) or "Almacs Food Group"


def ensure_stock(item_code: str, qty: float, warehouse: str = "Baked Goods - APC") -> str:
	"""Submit a Material Receipt to ensure *qty* units of *item_code* are available.

	Returns the generated Handling Unit name on the first SE item (may be None
	if BEAM HU tracking is disabled in the test environment).
	"""
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
	se.submit()
	return se.items[0].handling_unit


def ensure_stock_without_hu(
	item_code: str, qty: float, warehouse: str = "Baked Goods - APC"
) -> None:
	"""Submit a Material Receipt with BEAM HU tracking temporarily disabled.

	This represents stock for customers who use BEAM HUs only for outbound
	packing — receipts and inbound movements are not HU-tracked, so the
	Stock Ledger Entries carry no ``handling_unit`` value.  The setting is
	always restored to its original value even if the SE fails.
	"""
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
		assert not se.items[0].handling_unit, (
			"Test setup error: stock SE item should have no handling_unit " "when enable_handling_units=0"
		)
	finally:
		beam_settings.enable_handling_units = original
		beam_settings.save()


def create_so(customer: str, item_code: str, qty: int, warehouse: str = "Baked Goods - APC"):
	"""Create and submit a minimal Sales Order."""
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
	"""
	Build a draft Packing Slip from *dn_doc* and assign ``parcel_number``
	and ``ucc128`` to each item matching an entry in *sscc_by_item_code*.

	Items without a matching SSCC are left as-is (no parcel / no SSCC).
	Returns the saved (not submitted) Packing Slip document.
	"""
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


# ---------------------------------------------------------------------------
# Tests: create_handling_unit_for_sscc
# ---------------------------------------------------------------------------


def test_create_handling_unit_for_sscc():
	"""create_handling_unit_for_sscc must create a HU whose *name* IS the SSCC."""
	if frappe.db.exists("Handling Unit", SSCC_CREATE_TEST):
		frappe.delete_doc("Handling Unit", SSCC_CREATE_TEST, force=True)

	result = create_handling_unit_for_sscc(SSCC_CREATE_TEST)

	assert result == SSCC_CREATE_TEST

	hu = frappe.get_doc("Handling Unit", SSCC_CREATE_TEST)
	assert hu.name == SSCC_CREATE_TEST
	assert hu.handling_unit_name == SSCC_CREATE_TEST


def test_create_handling_unit_for_sscc_idempotent():
	"""Calling create_handling_unit_for_sscc twice must not raise."""
	if frappe.db.exists("Handling Unit", SSCC_IDEMPOTENT_TEST):
		frappe.delete_doc("Handling Unit", SSCC_IDEMPOTENT_TEST, force=True)

	r1 = create_handling_unit_for_sscc(SSCC_IDEMPOTENT_TEST)
	r2 = create_handling_unit_for_sscc(SSCC_IDEMPOTENT_TEST)  # HU already exists

	assert r1 == SSCC_IDEMPOTENT_TEST
	assert r2 == SSCC_IDEMPOTENT_TEST


# ---------------------------------------------------------------------------
# Tests: create_packing_slip_repack_entry (via Packing Slip submit hook)
# ---------------------------------------------------------------------------


@pytest.mark.order(20)
def test_repack_se_target_rows_use_sscc_as_handling_unit():
	"""
	Core regression test (Bug A + Bug B).

	After Packing Slip submit every target row in the Repack SE must carry
	the SSCC code as its ``handling_unit`` — not a BEAM UUID-derived value.

	Before the fix:
	  - Bug A: ``if ps_item.ucc128 and has_hu_on_psi`` caused all target rows
	    to fall into the ``else: is_finished_item = 1`` branch when PSI lacked
	    BEAM's HU inventory dimension, so BEAM generated UUID HUs.
	  - Bug B: ``frappe.db.set_value`` was overwritten by ``se.submit()``
	    → ``se.save()``, which re-saved the in-memory doc (still with
	    ``handling_unit = None``), so BEAM's hook never saw the SSCC.
	"""
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
	"""
	The Repack SE must be cost-balanced and source rows must have a non-zero
	``basic_rate`` derived from the SLE history (get_incoming_rate).
	"""
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
	"""
	Submitting a Packing Slip must create a Handling Unit document whose name
	IS the SSCC-18 string, enabling BEAM's barcode scan dispatcher to resolve
	a scanned GS1-128 label directly to this HU.
	"""
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
	"""``Packing Slip.repack_stock_entry`` must be populated after submit."""
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
	"""
	A Packing Slip whose items have parcel numbers but no ``ucc128`` values
	should not generate a Repack SE.

	``before_submit`` only requires that items have a ``parcel_number`` — it
	does not require ``ucc128``.  ``create_packing_slip_repack_entry`` filters
	to ``packed = [item … if item.parcel_number and item.ucc128]``, so items
	with a parcel but no SSCC are excluded and ``packed`` is empty → returns
	``None`` without creating a SE.
	"""
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
	"""
	Packing-slip-only HU workflow: the customer uses BEAM HUs exclusively for
	outbound packing (SSCC / GS1-128 labels) and does not HU-track receipts,
	transfers, or picks.  Delivery Note items therefore have
	``handling_unit = None``, which is the normal and expected state.

	The Packing Slip submit must still succeed:
	  - BEAM's ``validate_items_with_handling_unit`` hook is bypassed via
	    ``frappe.flags.beam_allow_source_rows_without_hu`` set in
	    ``create_packing_slip_repack_entry``.
	  - Source rows in the Repack SE carry no ``handling_unit`` (the expected
	    state for packing-slip-only HU customers).
	  - Target rows carry the SSCC codes as ``handling_unit``.
	  - The SE is cost-balanced.
	  - ``frappe.flags.beam_allow_source_rows_without_hu`` is reset to False
	    after submit (the finally block must not leak the flag).
	"""
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
