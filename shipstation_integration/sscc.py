# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model.naming import getseries

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)

SSCC_SERIES_SUFFIX = "SSCC"


def sscc_series_key(company_abbr: str) -> str:
	return f"{company_abbr}-{SSCC_SERIES_SUFFIX}"


def gs1_check_digit(digits: str) -> int:
	if len(digits) != 17 or not digits.isdigit():
		frappe.throw(
			_("gs1_check_digit requires exactly 17 numeric characters, got: {0}").format(repr(digits))
		)
	total = sum(int(d) * (1 if i % 2 else 3) for i, d in enumerate(digits))
	return (10 - (total % 10)) % 10


def next_sscc_serial(company_abbr: str, digits: int) -> str:
	return getseries(sscc_series_key(company_abbr), digits)


def generate_sscc(company_prefix: str, company_abbr: str, extension_digit: int = 0) -> str:
	if not company_prefix or not company_prefix.isdigit():
		frappe.throw(_("GS1 Company Prefix must be a non-empty string of digits"))
	prefix_len = len(company_prefix)
	if not (7 <= prefix_len <= 10):
		frappe.throw(_("GS1 Company Prefix must be 7–10 digits, got {0}").format(prefix_len))
	if not (0 <= extension_digit <= 9):
		frappe.throw(_("Extension digit must be 0–9"))
	serial_digits = 16 - prefix_len
	serial = next_sscc_serial(company_abbr, serial_digits)
	pre_check = str(extension_digit) + company_prefix + serial
	check = gs1_check_digit(pre_check)
	return pre_check + str(check)


def get_sscc_settings() -> "ShipstationSettings":
	candidates = frappe.get_all(
		"Shipstation Settings",
		filters={"enabled": 1},
		fields=["name", "gs1_company_prefix"],
		limit=1,
	)
	if not candidates or not candidates[0].gs1_company_prefix:
		frappe.throw(
			_(
				"No enabled Shipstation Settings found with a GS1 Company Prefix configured. "
				"Please set it under Shipstation Settings > GS1 / SSCC."
			)
		)
	return frappe.get_doc("Shipstation Settings", candidates[0].name)


def assign_sscc_codes(doc) -> list[str]:
	settings = get_sscc_settings()
	prefix = settings.gs1_company_prefix
	company = frappe.db.get_value("Delivery Note", doc.delivery_note, "company")
	abbr = frappe.db.get_value("Company", company, "abbr")
	parcels: dict[int, list] = {}
	for row in doc.items:
		if row.parcel_number:
			parcels.setdefault(row.parcel_number, []).append(row)
	new_codes: list[str] = []
	for rows in parcels.values():
		if any(r.ucc128 for r in rows):
			continue
		code = generate_sscc(prefix, abbr)
		for r in rows:
			r.ucc128 = code
		new_codes.append(code)
	return new_codes


@frappe.whitelist()
def generate_packing_slip_sscc(packing_slip: str) -> dict:
	doc = frappe.get_doc("Packing Slip", packing_slip)
	settings = get_sscc_settings()
	prefix = settings.gs1_company_prefix
	company = frappe.db.get_value("Delivery Note", doc.delivery_note, "company")
	abbr = frappe.db.get_value("Company", company, "abbr")
	parcels: dict[int, list] = {}
	for row in doc.items:
		if row.parcel_number:
			parcels.setdefault(row.parcel_number, []).append(row)
	generated = 0
	skipped = 0
	codes: list[dict] = []
	for rows in parcels.values():
		if any(r.ucc128 for r in rows):
			skipped += 1
			continue
		code = generate_sscc(prefix, abbr)
		for r in rows:
			r.ucc128 = code
			frappe.db.set_value("Packing Slip Item", r.name, "ucc128", code)
			codes.append({"name": r.name, "ucc128": code})
		generated += 1
	return {"generated": generated, "skipped": skipped, "codes": codes}
