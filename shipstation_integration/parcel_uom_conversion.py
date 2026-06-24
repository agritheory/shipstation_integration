# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Chained ERPNext UOM conversion for parcel display (Shipment Parcel Template, desk summaries).

Shipment parcel storage uses centimeters and kilograms internally; ERPNext fixtures often omit
direct Centimeter«»Inch factors but expose Meter-derived pairs. Delegate to Item.get_uom_conv_factor,
which resolves intermediate conversions."""

from functools import lru_cache

import frappe
from frappe import _

from erpnext.stock.doctype.item.item import get_uom_conv_factor

NORMALIZE_WEIGHT_FOR_LOOKUP = {
	"Kilogram": "Kg",
}

DIM_UOM_ABBR = {
	"Inch": '"',
	"Centimeter": "cm",
	"Foot": "'",
	"Millimeter": "mm",
	"Meter": "m",
}

WEIGHT_UOM_ABBR = {
	"Pound": "lbs",
	"Kg": "kg",
	"Kilogram": "kg",
	"Ounce": "oz",
	"Gram": "g",
}


def normalize_weight_uom_name(uom: str | None) -> str | None:
	if not uom:
		return uom
	return NORMALIZE_WEIGHT_FOR_LOOKUP.get(uom, uom)


def normalized_parcel_pair(from_uom: str | None, to_uom: str | None) -> tuple[str, str]:
	f = normalize_weight_uom_name(from_uom) or from_uom or ""
	t = normalize_weight_uom_name(to_uom) or to_uom or ""
	return (f, t)


@lru_cache(maxsize=256)
def parcel_uom_factor_cached(norm_from_uom: str, norm_to_uom: str) -> float:
	"""Ratio so: quantity_in_from_uom * factor == quantity_in_to_uom."""
	if norm_from_uom == norm_to_uom:
		return 1.0
	factor = get_uom_conv_factor(norm_from_uom, norm_to_uom)
	if factor is None:
		frappe.throw(
			_("No UOM conversion from {0} to {1}. Add a UOM Conversion Factor in Stock Settings.").format(
				frappe.bold(norm_from_uom),
				frappe.bold(norm_to_uom),
			)
		)
	return float(factor)


def parcel_uom_factor(from_uom: str | None, to_uom: str | None) -> float:
	f, t = normalized_parcel_pair(from_uom, to_uom)
	return parcel_uom_factor_cached(f, t)


def parcel_uom_factor_no_throw(from_uom: str | None, to_uom: str | None) -> float | None:
	try:
		return parcel_uom_factor(from_uom, to_uom)
	except Exception:
		return None


def conversion_factors_storage_to_prefs(
	storage_dimension_uom: str | None,
	storage_weight_uom: str | None,
	pref_dimension_uom: str,
	pref_weight_uom: str,
) -> dict[str, float | None]:
	"""Factors to multiply stored L/W/H and weight respectively for display/summary."""
	dim_from = storage_dimension_uom or "Centimeter"
	wt_from = normalize_weight_uom_name(storage_weight_uom) or "Kg"
	wt_pref = normalize_weight_uom_name(pref_weight_uom) or pref_weight_uom

	dim_f = parcel_uom_factor_no_throw(dim_from, pref_dimension_uom)
	wt_f = parcel_uom_factor_no_throw(wt_from, wt_pref)
	return {"dimension_factor": dim_f, "weight_factor": wt_f}


def format_parcel_details(row, user: str | None = None) -> str:
	"""Format parcel dimensions and weight for display from child row fields."""
	if not row:
		return ""

	if isinstance(row, str):
		row = frappe.parse_json(row)

	row = frappe._dict(row)
	if not row.get("parcel_template"):
		return ""

	length = row.get("parcel_length")
	width = row.get("parcel_width")
	height = row.get("parcel_height")
	weight = row.get("parcel_weight")

	if not any([length, width, height, weight]):
		return ""

	user_doc = frappe.get_cached_doc("User", user or frappe.session.user)
	dim_pref = user_doc.dimension_uom or "Centimeter"
	wt_pref = normalize_weight_uom_name(user_doc.weight_uom) or user_doc.weight_uom or "Kg"

	factors = conversion_factors_storage_to_prefs(
		row.get("dimension_uom") or "Centimeter",
		row.get("parcel_weight_uom") or "Kg",
		dim_pref,
		wt_pref,
	)
	dim_factor = factors.get("dimension_factor") or 1
	wt_factor = factors.get("weight_factor") or 1
	dim_abbr = DIM_UOM_ABBR.get(dim_pref) or dim_pref
	wt_abbr = WEIGHT_UOM_ABBR.get(wt_pref) or wt_pref

	parts: list[str] = []
	if length or width or height:

		def fmt(value: float) -> str:
			value = frappe.utils.flt(value)
			if value == int(value):
				return str(int(value))
			text = f"{value:.2f}".rstrip("0").rstrip(".")
			return text

		lv = frappe.utils.flt(length or 0) * dim_factor
		wv = frappe.utils.flt(width or 0) * dim_factor
		hv = frappe.utils.flt(height or 0) * dim_factor
		parts.append(f"{fmt(lv)}x{fmt(wv)}x{fmt(hv)}{dim_abbr}")

	if weight:
		wt_val = frappe.utils.flt(frappe.utils.flt(weight) * wt_factor, 2)
		parts.append(f"{wt_val}{wt_abbr}")

	return " ".join(parts)


@frappe.whitelist()
def get_parcel_detail_factors_for_storage_uoms(
	storage_dimension_uom: str | None = None,
	storage_weight_uom: str | None = None,
) -> dict[str, float | None]:
	"""Desk RPC when boot prefetch misses a rare storage UOM pair."""
	user = frappe.get_cached_doc("User", frappe.session.user)
	return conversion_factors_storage_to_prefs(
		storage_dimension_uom,
		storage_weight_uom,
		user.dimension_uom or "Centimeter",
		user.weight_uom or "Kg",
	)


def prefetch_parcel_factors_for_boot(
	user_dimension_pref: str,
	user_weight_pref: str,
) -> dict[str, dict[str, float]]:
	"""Build a lookup of storage UOM → factor toward the current user's prefs (for frappe.boot)."""
	dim_candidates = ["Centimeter", "Inch", "Meter", "Millimeter", "Foot"]
	wt_candidates = ["Kg", "Kilogram", "Pound", "Ounce", "Gram"]

	dim_prefs = user_dimension_pref or "Centimeter"
	wt_prefs = normalize_weight_uom_name(user_weight_pref) or user_weight_pref or "Kg"

	dim_factors: dict[str, float] = {}
	for du in dim_candidates:
		f = parcel_uom_factor_no_throw(du, dim_prefs)
		if f is not None:
			dim_factors[du] = f

	wt_factors: dict[str, float] = {}
	for w in wt_candidates:
		raw = normalize_weight_uom_name(w) or w
		fac = parcel_uom_factor_no_throw(raw, wt_prefs)
		if fac is not None:
			wt_factors[w] = fac

	return {"to_dimension_pref": dim_factors, "to_weight_pref": wt_factors}
