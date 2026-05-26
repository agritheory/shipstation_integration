# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from __future__ import annotations

import json

import frappe
from erpnext.stock.doctype.delivery_note.delivery_note import (
	make_packing_slip as erp_make_packing_slip,
	make_shipment as erp_make_shipment,
)
from frappe import _
from frappe.utils import cint, flt
from inventory_tools.cartonization import (
	get_physical_dimension,
	solve_cartonization,
)


def inventory_tools_cartonization_installed() -> bool:
	return "inventory_tools" in frappe.get_installed_apps()


def require_inventory_tools_cartonization():
	if not inventory_tools_cartonization_installed():
		frappe.throw(_("Install and enable the Inventory Tools app to use cartonization."))


def get_enabled_shipstation_cartonization_settings() -> frappe._dict | None:
	"""Return an enabled Shipstation Settings document that has cartonization turned on."""

	settings_names = frappe.get_all(
		"Shipstation Settings",
		filters={"enabled": 1, "enable_cartonization": 1},
		pluck="name",
		limit_page_length=1,
	)
	if not settings_names:
		return None

	return frappe.get_doc("Shipstation Settings", settings_names[0])


@frappe.whitelist()
def is_cartonization_enabled():
	"""Expose cartonization toggle for Packing Slip UI (authenticated users)."""
	return bool(get_enabled_shipstation_cartonization_settings())


def parse_container_doctypes_json(raw: str | None) -> list[str]:
	if not raw:
		return ["Shipment Parcel Template"]
	try:
		decoded = json.loads(raw)
	except (TypeError, json.JSONDecodeError):
		return ["Shipment Parcel Template"]
	if isinstance(decoded, list):
		return [str(x) for x in decoded if x]
	return ["Shipment Parcel Template"]


def solver_kwargs_from_shipstation_settings(ss_doc) -> dict:
	mode = ss_doc.get("cartonization_mode") or "3D Volumetric"
	return dict(
		mode=mode,
		default_mode=mode,
		allow_rotation=int(ss_doc.get("cartonization_allow_rotation") or 0) == 1,
		solver_timeout=int(ss_doc.get("cartonization_solver_timeout_seconds") or 30),
	)


def company_from_packing_slip(ps) -> str | None:
	"""ERPNext Packing Slip has no Company field; resolve it from the linked Delivery Note."""
	dn = getattr(ps, "delivery_note", None)
	if dn:
		return frappe.db.get_value("Delivery Note", dn, "company")
	return frappe.defaults.get_user_default("Company")


def stable_child_row_key_for_cartonization(row) -> str | None:
	"""Stable key for mapped/unsaved child rows before ``name`` or ``dn_detail`` exist."""
	n = getattr(row, "name", None)
	if n:
		return n
	for attr in ("dn_detail", "pi_detail"):
		v = getattr(row, attr, None)
		if v:
			return v
	idx = getattr(row, "idx", None)
	if idx is not None:
		return f"child_row_idx_{int(idx)}"
	return None


def cartonization_items_from_packing_slip_items(
	ps_items: list, restrict_row_names: set[str] | None = None
) -> list[dict]:
	out = []
	for row in ps_items or []:
		if getattr(row, "parcel_number", None):
			continue
		stable_key = stable_child_row_key_for_cartonization(row)
		if not stable_key:
			continue
		if restrict_row_names and stable_key not in restrict_row_names:
			continue

		out.append(
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"uom": getattr(row, "uom", None),
				"stock_uom": getattr(row, "stock_uom", None),
				"conversion_factor": getattr(row, "conversion_factor", None),
				"name": stable_key,
				"stock_qty": getattr(row, "stock_qty", None),
				"dn_detail": getattr(row, "dn_detail", None),
			}
		)
	return out


def cartonization_items_from_shipment_delivery_note_rows(
	sdn_rows: list, restrict_row_names: set[str] | None = None
) -> list[dict]:
	out = []
	for row in sdn_rows or []:
		if getattr(row, "parcel_number", None):
			continue
		if not getattr(row, "item_code", None):
			continue
		stable_key = stable_child_row_key_for_cartonization(row)
		if not stable_key:
			continue
		if restrict_row_names and stable_key not in restrict_row_names:
			continue

		out.append(
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"uom": getattr(row, "uom", None),
				"stock_uom": getattr(row, "stock_uom", None),
				"name": stable_key,
				"dn_detail": getattr(row, "dn_detail", None),
			}
		)
	return out


def cartonization_items_from_delivery_note_items(dn_items: list) -> list[dict]:
	out = []
	for row in dn_items or []:
		out.append(
			{
				"item_code": row.item_code,
				"qty": row.qty,
				"uom": getattr(row, "uom", None),
				"stock_uom": getattr(row, "stock_uom", None),
				"name": row.name,
				"conversion_factor": getattr(row, "conversion_factor", None),
			}
		)
	return out


def apply_parcel_template_to_row(row_doc, parcel_template_name: str | None):
	if not parcel_template_name:
		return

	pt_name = parcel_template_name
	if not frappe.db.exists("Shipment Parcel Template", pt_name):
		resolved = frappe.db.get_value(
			"Shipment Parcel Template", {"parcel_template_name": parcel_template_name}, "name"
		)
		pt_name = resolved

	if not pt_name:
		return

	row_doc.parcel_template = pt_name

	pd = get_physical_dimension("Shipment Parcel Template", pt_name, "Interior")
	if pd and pd.get("item_length"):
		row_doc.parcel_length = flt(pd.get("item_length"))
		row_doc.parcel_width = flt(pd.get("item_width"))
		row_doc.parcel_height = flt(pd.get("item_height"))
		if pd.get("uom"):
			row_doc.dimension_uom = pd.get("uom")
	else:
		pt = frappe.get_cached_doc("Shipment Parcel Template", pt_name)
		row_doc.parcel_length = pt.length
		row_doc.parcel_width = pt.width
		row_doc.parcel_height = pt.height
		row_doc.dimension_uom = "Centimeter"


def cartonize_mapped_packing_slip_from_delivery_note(ps):
	if not inventory_tools_cartonization_installed():
		return

	ss = get_enabled_shipstation_cartonization_settings()
	if not ss or not cint(ss.get("auto_cartonize_packing_slip")):
		return

	require_inventory_tools_cartonization()

	items = cartonization_items_from_packing_slip_items(ps.items)
	if not items:
		return

	types = parse_container_doctypes_json(ss.get("default_container_doctypes_json"))
	kwargs = solver_kwargs_from_shipstation_settings(ss)

	company_ps = company_from_packing_slip(ps)
	solution = solve_cartonization(
		items,
		container_doctypes=types,
		company=company_ps,
		settings=kwargs,
	)
	assign_bins_to_child_rows(ps, "items", solution.get("bins"))


def assign_bins_to_child_rows(doc, child_table_field: str, bins: list):
	rows_list = doc.get(child_table_field) or []
	row_index = {}
	for r in rows_list:
		sk = stable_child_row_key_for_cartonization(r)
		if sk:
			row_index[sk] = r
		if getattr(r, "name", None):
			row_index[r.name] = r
		dn_det = getattr(r, "dn_detail", None)
		if dn_det:
			row_index[dn_det] = r
		pi_det = getattr(r, "pi_detail", None)
		if pi_det:
			row_index[pi_det] = r

	# Build mapping: row_name → [(parcel_no, parcel_template, packed_item), ...]
	row_bins: dict[str, list] = {}
	parcel_no = 1
	for bn in bins or []:
		for packed in bn.get("items") or []:
			row_key = packed.get("row_name") or packed.get("dn_detail") or packed.get("pi_detail")
			if not row_key or row_key == "__best_fit_probe__":
				continue
			if row_key not in row_index:
				continue
			row_bins.setdefault(row_key, []).append((parcel_no, bn.get("parcel_template"), packed))
		parcel_no += 1

	rows_to_remove = []
	rows_to_add = []

	for row_key, assignments in row_bins.items():
		target_row = row_index[row_key]
		if len(assignments) == 1:
			pno, tmpl, _ = assignments[0]
			target_row.parcel_number = pno
			apply_parcel_template_to_row(target_row, tmpl)
		else:
			# Row was split across multiple bins — replace original with one row per bin.
			rows_to_remove.append(target_row)
			for pno, tmpl, packed in assignments:
				chunk_qty = flt(packed.get("qty") or 0) or flt(target_row.qty) / len(assignments)
				rows_to_add.append(
					{
						"source_row": target_row,
						"qty": chunk_qty,
						"parcel_number": pno,
						"parcel_template": tmpl,
					}
				)

	for row in rows_to_remove:
		doc.remove(row)

	for entry in rows_to_add:
		src = entry["source_row"]
		# Copy all relevant fields from the source row.
		new_row = doc.append(
			child_table_field,
			{
				"item_code": getattr(src, "item_code", None),
				"item_name": getattr(src, "item_name", None),
				"description": getattr(src, "description", None),
				"qty": entry["qty"],
				"stock_uom": getattr(src, "stock_uom", None),
				"dn_detail": getattr(src, "dn_detail", None),
				"parcel_number": entry["parcel_number"],
			},
		)
		apply_parcel_template_to_row(new_row, entry["parcel_template"])


@frappe.whitelist()
def cartonize_items(
	items_json: str,
	container_doctypes: str | None = None,
	company: str | None = None,
):
	"""Pack plain item rows JSON into bins using Shipstation / Inventory Tools settings."""

	require_inventory_tools_cartonization()

	try:
		items = json.loads(items_json or "[]")
	except json.JSONDecodeError:
		frappe.throw(_("Invalid items JSON"))

	types = json.loads(container_doctypes) if container_doctypes else None

	ss = get_enabled_shipstation_cartonization_settings()
	if not types and ss:
		types = parse_container_doctypes_json(ss.get("default_container_doctypes_json"))

	kwargs = solver_kwargs_from_shipstation_settings(ss) if ss else {}

	return solve_cartonization(
		items,
		container_doctypes=types,
		company=company,
		settings=kwargs,
	)


@frappe.whitelist()
def apply_cartonization_to_packing_slip(packing_slip_name: str, row_names_json: str | None = None):
	require_inventory_tools_cartonization()

	restrict = None
	if row_names_json:
		try:
			restrict = set(json.loads(row_names_json))
		except json.JSONDecodeError:
			frappe.throw(_("Invalid row_names_json"))

	ps = frappe.get_doc("Packing Slip", packing_slip_name)
	items = cartonization_items_from_packing_slip_items(ps.items, restrict)
	if not items:
		return {"bins": [], "messages": [str(_("No unpackaged rows to cartonize."))]}

	ss = get_enabled_shipstation_cartonization_settings()
	if not ss:
		frappe.throw(_("Enable cartonization in Shipstation Settings to cartonize packing slips."))
	assert ss is not None

	types = parse_container_doctypes_json(ss.get("default_container_doctypes_json"))
	kwargs = solver_kwargs_from_shipstation_settings(ss)

	solution = solve_cartonization(
		items,
		container_doctypes=types,
		company=company_from_packing_slip(ps),
		settings=kwargs,
	)
	assign_bins_to_child_rows(ps, "items", solution.get("bins"))
	ps.save()
	return solution


@frappe.whitelist()
def apply_cartonization_to_shipment(shipment_name: str, row_names_json: str | None = None):
	require_inventory_tools_cartonization()

	restrict = None
	if row_names_json:
		try:
			restrict = set(json.loads(row_names_json))
		except json.JSONDecodeError:
			frappe.throw(_("Invalid row_names_json"))

	sh = frappe.get_doc("Shipment", shipment_name)
	items = cartonization_items_from_shipment_delivery_note_rows(sh.shipment_delivery_note, restrict)
	if not items:
		return {
			"bins": [],
			"messages": [str(_("No unpackaged Shipment Delivery Note rows to cartonize."))],
		}

	ss = get_enabled_shipstation_cartonization_settings()
	if not ss:
		frappe.throw(_("Enable cartonization in Shipstation Settings to cartonize shipments."))
	assert ss is not None

	types = parse_container_doctypes_json(ss.get("default_container_doctypes_json"))

	company = sh.pickup_company or sh.delivery_company or frappe.defaults.get_user_default("Company")
	kwargs = solver_kwargs_from_shipstation_settings(ss)

	solution = solve_cartonization(items, container_doctypes=types, company=company, settings=kwargs)

	assign_bins_to_child_rows(sh, "shipment_delivery_note", solution.get("bins"))
	sh.save()

	return solution


@frappe.whitelist()
def preview_cartonization_for_delivery_note(delivery_note_name: str):
	require_inventory_tools_cartonization()

	dn = frappe.get_doc("Delivery Note", delivery_note_name)
	items = cartonization_items_from_delivery_note_items(dn.items)

	ss = get_enabled_shipstation_cartonization_settings()
	types = parse_container_doctypes_json(ss.get("default_container_doctypes_json") if ss else None)

	kwargs = solver_kwargs_from_shipstation_settings(ss) if ss else {}

	return solve_cartonization(
		items,
		container_doctypes=types,
		company=dn.company,
		settings=kwargs,
	)


@frappe.whitelist()
def insert_cartonized_packing_slip_from_delivery_note(delivery_note_name: str):
	"""Insert a Packing Slip created from ``make_packing_slip`` (cartonization runs in ``after_mapping``)."""

	ps = erp_make_packing_slip(delivery_note_name)
	ps.insert()

	return ps.name


def auto_cartonize_shipment_document(sh):
	if not inventory_tools_cartonization_installed():
		return

	ss_list = frappe.get_all(
		"Shipstation Settings",
		filters={"enabled": 1, "enable_cartonization": 1, "auto_cartonize_shipment": 1},
		limit_page_length=1,
	)
	if not ss_list:
		return

	require_inventory_tools_cartonization()

	ss = frappe.get_doc("Shipstation Settings", ss_list[0].name)
	items = cartonization_items_from_shipment_delivery_note_rows(sh.shipment_delivery_note)
	if not items:
		return

	types = parse_container_doctypes_json(ss.get("default_container_doctypes_json"))
	kwargs = solver_kwargs_from_shipstation_settings(ss)
	company = sh.pickup_company or sh.delivery_company or frappe.defaults.get_user_default("Company")

	solution = solve_cartonization(items, container_doctypes=types, company=company, settings=kwargs)
	assign_bins_to_child_rows(sh, "shipment_delivery_note", solution.get("bins"))


@frappe.whitelist()
def make_packing_slip_with_optional_cartonization(source_name, target_doc=None):
	"""Whitelisted override for ``erpnext...make_packing_slip``.
	Packing Slip cartonization from a Delivery Note runs in ``ShipstationPackingSlip.after_mapping``."""

	return erp_make_packing_slip(source_name, target_doc)


@frappe.whitelist()
def make_shipment_with_optional_cartonization(source_name, target_doc=None):
	sh = erp_make_shipment(source_name, target_doc)
	auto_cartonize_shipment_document(sh)
	return sh
