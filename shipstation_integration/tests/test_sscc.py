# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Tests for SSCC-18 / UCC-128 generation."""

import frappe
import pytest

from shipstation_integration.sscc import (
	generate_packing_slip_sscc,
	generate_sscc,
	gs1_check_digit,
)

ABBR = "CFC"
TEST_PREFIX = "0614141"


# ---------------------------------------------------------------------------
# gs1_check_digit — pure function, no DB required
# ---------------------------------------------------------------------------


def test_gs1_check_digit():
	# GS1 published example: extension 0, prefix 0614141, serial 000000001
	assert gs1_check_digit("00614141000000001") == 2
	# sum=0 → (10-0)%10 = 0
	assert gs1_check_digit("00000000000000000") == 0
	# i=1 (weight 1) → 9; i=3 (weight 1) → 1; sum=10 → check=0
	assert gs1_check_digit("09010000000000000") == 0

	with pytest.raises(Exception):
		gs1_check_digit("0061414100000000X")  # non-digit
	with pytest.raises(Exception):
		gs1_check_digit("0061414100000001")  # 16 chars
	with pytest.raises(Exception):
		gs1_check_digit("")


# ---------------------------------------------------------------------------
# generate_sscc — requires DB (tabSeries write)
# ---------------------------------------------------------------------------


def test_generate_sscc(db_instance):
	# length, all-digits, valid check digit
	result = generate_sscc(TEST_PREFIX, ABBR)
	assert len(result) == 18
	assert result.isdigit()
	assert gs1_check_digit(result[:17]) == int(result[17])

	# serial increments → uniqueness
	assert result != generate_sscc(TEST_PREFIX, ABBR)

	# all valid prefix lengths produce an 18-digit SSCC with a valid check digit
	for prefix in ("1234567", "12345678", "123456789", "1234567890"):
		r = generate_sscc(prefix, ABBR)
		assert len(r) == 18
		assert gs1_check_digit(r[:17]) == int(r[17])

	# extension digit is encoded in position 0
	assert generate_sscc(TEST_PREFIX, ABBR, extension_digit=3)[0] == "3"

	# invalid inputs raise
	with pytest.raises(Exception):
		generate_sscc("ABCDEFG", ABBR)
	with pytest.raises(Exception):
		generate_sscc("123456", ABBR)  # too short
	with pytest.raises(Exception):
		generate_sscc("12345678901", ABBR)  # too long


# ---------------------------------------------------------------------------
# generate_packing_slip_sscc — integration, requires db_instance
# ---------------------------------------------------------------------------


@pytest.fixture
def packing_slip(db_instance):
	"""Return the draft test Packing Slip with all SSCC values cleared.

	Filtering for docstatus=0 ensures we always get the setup-created draft
	PS rather than any submitted PS produced by other test modules.
	"""
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()
	for row in ps.items:
		row.ucc128 = None
	ps.save()
	yield ps
	ps.reload()


def test_generate_packing_slip_sscc_populates_rows(packing_slip):
	result = generate_packing_slip_sscc(packing_slip.name)

	assert result["generated"] >= 1
	assert result["skipped"] == 0

	packing_slip.reload()
	# All items in parcel 1 get the same SSCC
	parcel_1_items = [r for r in packing_slip.items if r.parcel_number == 1]
	assert parcel_1_items
	sscc_values = {r.ucc128 for r in parcel_1_items}
	assert len(sscc_values) == 1  # all items in the same parcel share one SSCC
	code = sscc_values.pop()
	assert len(code) == 18
	assert code.isdigit()
	assert gs1_check_digit(code[:17]) == int(code[17])


def test_generate_packing_slip_sscc_skips_existing(packing_slip):
	existing = "006141410000000012"
	packing_slip.reload()
	packing_slip.items[0].ucc128 = existing
	packing_slip.save()

	result = generate_packing_slip_sscc(packing_slip.name)

	assert result["skipped"] >= 1
	packing_slip.reload()
	assert packing_slip.items[0].ucc128 == existing


def test_generate_packing_slip_sscc_skips_rows_without_parcel_number(packing_slip):
	packing_slip.reload()
	for row in packing_slip.items:
		row.parcel_number = 0
	packing_slip.save()

	result = generate_packing_slip_sscc(packing_slip.name)

	assert result["generated"] == 0
	assert result["skipped"] == 0
	packing_slip.reload()
	assert not any(r.ucc128 for r in packing_slip.items)


def test_generate_packing_slip_sscc_no_parcel_numbers(packing_slip):
	packing_slip.reload()
	original_parcel_numbers = {r.name: r.parcel_number for r in packing_slip.items}

	for row in packing_slip.items:
		row.parcel_number = 0
	packing_slip.save()

	result = generate_packing_slip_sscc(packing_slip.name)
	assert result["generated"] == 0
	assert result["skipped"] == 0

	# Restore parcel numbers
	packing_slip.reload()
	for row in packing_slip.items:
		row.parcel_number = original_parcel_numbers.get(row.name, 0)
	packing_slip.save()
