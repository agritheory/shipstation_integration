# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from shipstation_integration.shipstation_integration.overrides.sscc import (
	generate_packing_slip_sscc,
	gs1_check_digit,
)


def get_draft_packing_slip():
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()
	for row in ps.items:
		row.ucc128 = None
	ps.save()
	return ps


@pytest.mark.order(90)
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


@pytest.mark.order(91)
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

	for row in ps.items:
		row.ucc128 = None
	ps.save()
