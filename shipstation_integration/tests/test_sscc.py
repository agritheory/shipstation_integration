# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from shipstation_integration.sscc import (
	generate_packing_slip_sscc,
	generate_sscc,
	gs1_check_digit,
)

ABBR = "CFC"
TEST_PREFIX = "0614141"


def get_draft_packing_slip():
	"""Return the last draft Packing Slip with all ucc128 fields cleared."""
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()
	for row in ps.items:
		row.ucc128 = None
	ps.save()
	return ps


def test_gs1_check_digit():
	assert gs1_check_digit("00614141000000001") == 2
	assert gs1_check_digit("00000000000000000") == 0
	assert gs1_check_digit("09010000000000000") == 0

	with pytest.raises(Exception):
		gs1_check_digit("0061414100000000X")
	with pytest.raises(Exception):
		gs1_check_digit("0061414100000001")
	with pytest.raises(Exception):
		gs1_check_digit("")


def test_generate_sscc():
	result = generate_sscc(TEST_PREFIX, ABBR)
	assert len(result) == 18
	assert result.isdigit()
	assert gs1_check_digit(result[:17]) == int(result[17])

	assert result != generate_sscc(TEST_PREFIX, ABBR)

	for prefix in ("1234567", "12345678", "123456789", "1234567890"):
		r = generate_sscc(prefix, ABBR)
		assert len(r) == 18
		assert gs1_check_digit(r[:17]) == int(r[17])

	assert generate_sscc(TEST_PREFIX, ABBR, extension_digit=3)[0] == "3"

	with pytest.raises(Exception):
		generate_sscc("ABCDEFG", ABBR)
	with pytest.raises(Exception):
		generate_sscc("123456", ABBR)
	with pytest.raises(Exception):
		generate_sscc("12345678901", ABBR)


def test_generate_packing_slip_sscc_populates_rows():
	ps = get_draft_packing_slip()

	result = generate_packing_slip_sscc(ps.name)

	assert result["generated"] >= 1
	assert result["skipped"] == 0

	ps.reload()
	parcel_1_items = [r for r in ps.items if r.parcel_number == 1]
	assert parcel_1_items
	sscc_values = {r.ucc128 for r in parcel_1_items}
	assert len(sscc_values) == 1
	code = sscc_values.pop()
	assert len(code) == 18
	assert code.isdigit()
	assert gs1_check_digit(code[:17]) == int(code[17])

	# Cleanup
	for row in ps.items:
		row.ucc128 = None
	ps.save()


def test_generate_packing_slip_sscc_skips_existing():
	ps = get_draft_packing_slip()
	existing = "006141410000000012"
	ps.items[0].ucc128 = existing
	ps.save()

	result = generate_packing_slip_sscc(ps.name)

	assert result["skipped"] >= 1
	ps.reload()
	assert ps.items[0].ucc128 == existing

	# Cleanup
	for row in ps.items:
		row.ucc128 = None
	ps.save()


def test_generate_packing_slip_sscc_skips_rows_without_parcel_number():
	ps = get_draft_packing_slip()
	original_parcel_numbers = {r.name: r.parcel_number for r in ps.items}
	for row in ps.items:
		row.parcel_number = 0
	ps.save()

	result = generate_packing_slip_sscc(ps.name)

	assert result["generated"] == 0
	assert result["skipped"] == 0
	ps.reload()
	assert not any(r.ucc128 for r in ps.items)

	# Restore parcel numbers
	for row in ps.items:
		row.parcel_number = original_parcel_numbers.get(row.name, 0)
	ps.save()


def test_generate_packing_slip_sscc_no_parcel_numbers():
	ps = get_draft_packing_slip()
	original_parcel_numbers = {r.name: r.parcel_number for r in ps.items}
	for row in ps.items:
		row.parcel_number = 0
	ps.save()

	result = generate_packing_slip_sscc(ps.name)
	assert result["generated"] == 0
	assert result["skipped"] == 0

	# Restore parcel numbers
	ps.reload()
	for row in ps.items:
		row.parcel_number = original_parcel_numbers.get(row.name, 0)
	ps.save()
