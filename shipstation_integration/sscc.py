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
	"""Return the tabSeries key for a given ERPNext company abbreviation.

	Format: ``{abbr}-SSCC`` (e.g. ``CFC-SSCC``), matching the ERPNext convention
	for company-scoped naming series. Each company maintains an independent counter
	so multi-company instances never share serials across GS1 prefixes.
	"""
	return f"{company_abbr}-{SSCC_SERIES_SUFFIX}"


def gs1_check_digit(digits: str) -> int:
	"""Compute the GS1 mod-10 check digit for a string of digits.

	Expects exactly 17 digits (the pre-check portion of an SSCC-18).
	Even positions (0-indexed: 0, 2, 4 ...) are multiplied by 3;
	odd positions (1, 3, 5 ...) are multiplied by 1.
	Check digit = (10 - (sum % 10)) % 10.
	"""
	if len(digits) != 17 or not digits.isdigit():
		frappe.throw(
			_("gs1_check_digit requires exactly 17 numeric characters, got: {0}").format(repr(digits))
		)

	# GS1 spec: multiply by 3 at even positions (0-indexed from left), 1 at odd positions.
	# Equivalently: rightmost data digit gets ×3, alternating left.
	total = sum(int(d) * (1 if i % 2 else 3) for i, d in enumerate(digits))
	return (10 - (total % 10)) % 10


def next_sscc_serial(company_abbr: str, digits: int) -> str:
	"""Atomically increment the per-company SSCC counter in tabSeries and return the next value.

	Delegates to ``frappe.model.naming.getseries`` which uses ``frappe.qb`` with
	``FOR UPDATE`` to prevent collisions when multiple documents are saved concurrently.
	Shared across all doctypes (Packing Slip today, Shipment in the future).
	"""
	return getseries(sscc_series_key(company_abbr), digits)


def generate_sscc(company_prefix: str, company_abbr: str, extension_digit: int = 0) -> str:
	"""Generate a valid 18-digit SSCC-18 string.

	Structure:
	    Extension(1) + GS1Prefix(7-10) + Serial(16 - len(prefix)) + CheckDigit(1) = 18

	Args:
	    company_prefix: Licensed GS1 Company Prefix (7–10 digits).
	    company_abbr: ERPNext company abbreviation (e.g. ``"CFC"``). Scopes the
	        ``tabSeries`` counter so each company has an independent serial pool.
	    extension_digit: Single digit (0–9) prepended to the SSCC. Defaults to 0.

	Returns:
	    18-character string of digits suitable for encoding in a GS1-128 barcode
	    with Application Identifier (00).
	"""
	if not company_prefix or not company_prefix.isdigit():
		frappe.throw(_("GS1 Company Prefix must be a non-empty string of digits"))

	prefix_len = len(company_prefix)
	if not (7 <= prefix_len <= 10):
		frappe.throw(_("GS1 Company Prefix must be 7–10 digits, got {0}").format(prefix_len))

	if not (0 <= extension_digit <= 9):
		frappe.throw(_("Extension digit must be 0–9"))

	serial_digits = 16 - prefix_len
	serial = next_sscc_serial(company_abbr, serial_digits)

	pre_check = str(extension_digit) + company_prefix + serial  # 17 digits
	check = gs1_check_digit(pre_check)
	return pre_check + str(check)


def get_sscc_settings() -> "ShipstationSettings":
	"""Return the first enabled Shipstation Settings doc that has a GS1 prefix configured."""
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
	"""Assign SSCC-18 codes to all packed parcels that don't have one yet.

	Intended for use inside a ``before_submit`` hook.  Operates directly on
	the in-memory ``doc`` without calling ``doc.save()``; Frappe persists the
	changes as part of the submit transaction.

	See also: ``generate_packing_slip_sscc`` — the whitelisted counterpart
	that writes directly to the database and is called from the UI button.

	Returns:
	    List of newly generated SSCC codes (empty if nothing was generated).
	"""
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
	"""Generate and persist UCC-128 / SSCC-18 codes for each unique parcel in a Packing Slip.

	Called from the "Generate SSCC" UI button on the Packing Slip form.  Items
	are grouped by ``parcel_number``; one SSCC is issued per unique parcel and
	written to every item in that parcel.  Parcels whose items already carry a
	``ucc128`` value are skipped.  Items without a ``parcel_number`` are ignored.

	The GS1 serial counter in ``tabSeries`` is incremented for every code
	issued.  Consumed serials are never reused, even if the Packing Slip is
	later discarded — this is intentional and guarantees uniqueness within a
	GS1 prefix.

	Generated codes are written to the database immediately via
	``frappe.db.set_value`` on each ``Packing Slip Item`` row.

	See also: ``assign_sscc_codes`` — the in-memory counterpart used inside
	the ``before_submit`` hook.

	Args:
	    packing_slip: Name of the Packing Slip document.

	Returns:
	    dict with:
	      ``generated``  – count of newly issued SSCCs
	      ``skipped``    – count of parcels that already had an SSCC
	      ``codes``      – list of ``{"name": row_name, "ucc128": code}`` for
	                       every item row that received a new code
	"""
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
