# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Cartonization behavior for Shipstation-backed packing slips and shipments.

The indexed ``customers`` list comes from ``beam.tests.fixtures`` and matches
``shipstation_integration.tests.setup`` sales / delivery scenarios: for example
``customers[2]`` is Cafe 27 Cafeteria (bulk pie DN for solver tests) and
``customers[1]`` is Beans and Dreams Roasters Boston (small-parcel DN). Tests use
those indices with ``get_so_with_items`` so each case lands on the right seeded
Sales Order and Delivery Note.
"""

import pytest

import frappe
from beam.tests.fixtures import customers
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from frappe.utils import cint
from inventory_tools.cartonization import solve_cartonization

import shipstation_integration.cartonization as ss_cart
from shipstation_integration.cartonization import apply_cartonization_to_packing_slip
from shipstation_integration.tests.setup import get_so_with_items


@pytest.mark.order(115)
def test_default_container_doctypes_json_falls_back_and_respects_explicit_list():
	"""
	Shipstation Settings ``default_container_doctypes_json`` decides which container
	reference doctypes the solver may use. Missing or unusable values must fall
	back to Shipment Parcel Template so cartonization is never left without a pool;
	a valid JSON array must be honored verbatim so sites can add doctypes such as
	``Vehicle`` alongside parcel templates.
	"""
	assert ss_cart.parse_container_doctypes_json(None) == ["Shipment Parcel Template"]
	assert ss_cart.parse_container_doctypes_json("") == ["Shipment Parcel Template"]
	assert ss_cart.parse_container_doctypes_json("not-valid-json-{") == ["Shipment Parcel Template"]
	assert ss_cart.parse_container_doctypes_json("{}") == ["Shipment Parcel Template"]

	assert ss_cart.parse_container_doctypes_json('["Vehicle"]') == ["Vehicle"]
	assert ss_cart.parse_container_doctypes_json('["Shipment Parcel Template", "Vehicle"]') == [
		"Shipment Parcel Template",
		"Vehicle",
	]


@pytest.mark.order(116)
def test_cartonize_packing_slip_cafe27_pies_into_triple_stack_boxes():
	"""
	20 pies (10 × Gooseberry Pie + 10 × Kaduka Key Lime Pie) from the Cafe 27
	Cafeteria Delivery Note are packed into "Pie Triple Stack" boxes when the solver
	is restricted to that template via reference_document_filters.

	Each box holds exactly 3 standard 12" pies stacked vertically:
	  Interior:  30.48 × 30.48 × 30.48 cm = 0.3048³ m³
	  Pie box:   30.48 × 30.48 × 10.16 cm = 0.3048 × 0.3048 × 0.1016 m³

	The solver pre-splits each 10-unit row into chunks of 3 (3, 3, 3, 1) before
	the First-Fit-Decreasing pass, then packs three pies per Pie Triple Stack bin.
	For 20 pies total that yields 7 bins (six with qty 3, one with qty 2) and
	nothing skipped.
	"""
	so_name = get_so_with_items(
		customers[2], "Ambrosia Pie Company", ["Gooseberry Pie", "Kaduka Key Lime Pie"]
	)
	assert so_name, "Sales Order for Cafe 27 Cafeteria must exist (run before_test first)"

	dn_name = frappe.db.get_value(
		"Delivery Note Item", {"against_sales_order": so_name, "docstatus": 0}, "parent"
	)
	assert dn_name, "Delivery Note for Cafe 27 Cafeteria must exist (run before_test first)"

	assert frappe.db.exists(
		"Shipment Parcel Template", "Pie Triple Stack"
	), "Pie Triple Stack parcel template must exist (run before_test first)"
	assert frappe.db.exists(
		"Physical Dimension",
		{
			"reference_doctype": "Shipment Parcel Template",
			"reference_document": "Pie Triple Stack",
			"dimension_type": "Interior",
		},
	), "Interior Physical Dimension for Pie Triple Stack must exist (run before_test first)"

	ps = make_packing_slip(dn_name)
	ps.insert()

	try:
		items = ss_cart.cartonization_items_from_packing_slip_items(ps.items)
		assert len(items) == 2, "Packing Slip should have 2 unpacked lines (one per item)"

		solution = solve_cartonization(
			items,
			container_doctypes=["Shipment Parcel Template"],
			settings={"mode": "3D Volumetric"},
			reference_document_filters={"Shipment Parcel Template": ["Pie Triple Stack"]},
		)

		bins = solution.get("bins") or []
		skipped = solution.get("skipped") or []

		assert not skipped, f"All pies should be placed; skipped: {skipped}"

		for b in bins:
			assert (b.get("container") or {}).get(
				"name"
			) == "Pie Triple Stack", (
				f"Bin {b.get('bin_number')} used unexpected template: {b.get('parcel_template')}"
			)

		total_qty = sum(item.get("qty", 0) for b in bins for item in (b.get("items") or []))
		assert total_qty == pytest.approx(20.0), f"Expected qty_line total 20.0, got {total_qty}"

		assert len(bins) == 7, f"Expected 7 bins (6 × 3 + 1 × 2), got {len(bins)}"

		bin_qtys = sorted(sum(item.get("qty", 0) for item in (b.get("items") or [])) for b in bins)
		assert bin_qtys.count(3) == 6, f"Expected six bins with 3 pies, got qtys {bin_qtys}"
		assert bin_qtys.count(2) == 1, f"Expected one bin with 2 pies, got qtys {bin_qtys}"

		for b in bins:
			bin_qty = sum(item.get("qty", 0) for item in (b.get("items") or []))
			assert bin_qty <= 3 + 1e-9, f"Bin {b.get('bin_number')} holds {bin_qty} pies (max 3)"

	finally:
		frappe.delete_doc("Packing Slip", ps.name, force=True)


@pytest.mark.order(117)
def test_apply_cartonization_to_packing_slip_splits_rows_into_bins():
	"""
	Multi-bin packing replaces each source Packing Slip Item with one row per
	physical parcel where needed (qty, parcel_number, parcel_template). For two
	10-unit pie lines and Pie Triple Stack, the solver yields 7 parcels (mostly
	3 pies each) and 8 item rows (four per pie line: 3 + 3 + 3 + 1).
	"""
	so_name = get_so_with_items(
		customers[2], "Ambrosia Pie Company", ["Gooseberry Pie", "Kaduka Key Lime Pie"]
	)
	assert so_name, "Sales Order for Cafe 27 Cafeteria must exist (run before_test first)"

	dn_name = frappe.db.get_value(
		"Delivery Note Item", {"against_sales_order": so_name, "docstatus": 0}, "parent"
	)
	assert dn_name, "Delivery Note for Cafe 27 Cafeteria must exist (run before_test first)"

	ps = make_packing_slip(dn_name)
	ps.insert()

	try:
		apply_cartonization_to_packing_slip(ps.name)
		ps.reload()

		assert len(ps.items) == 8, f"Expected 8 rows (4 per item) after cartonize, got {len(ps.items)}"

		parcel_numbers = sorted({row.parcel_number for row in ps.items})
		assert parcel_numbers == list(range(1, 8)), f"Expected parcel numbers 1-7, got {parcel_numbers}"

		for row in ps.items:
			assert row.parcel_template == "Pie Triple Stack", (
				f"{row.item_code} parcel {row.parcel_number}: "
				f"expected Pie Triple Stack, got {row.parcel_template}"
			)
			assert row.qty in (1, 3), (
				f"{row.item_code} parcel {row.parcel_number}: " f"expected qty 1 or 3, got {row.qty}"
			)

		qty_by_item: dict[str, float] = {}
		for row in ps.items:
			qty_by_item[row.item_code] = qty_by_item.get(row.item_code, 0) + row.qty

		assert qty_by_item["Gooseberry Pie"] == pytest.approx(10.0)
		assert qty_by_item["Kaduka Key Lime Pie"] == pytest.approx(10.0)

		parcels_by_item: dict[str, set[int]] = {}
		for row in ps.items:
			parcels_by_item.setdefault(row.item_code, set()).add(row.parcel_number)
		assert len(parcels_by_item["Gooseberry Pie"]) == 4
		assert len(parcels_by_item["Kaduka Key Lime Pie"]) == 4

		qtys_by_item: dict[str, list[float]] = {}
		for row in ps.items:
			qtys_by_item.setdefault(row.item_code, []).append(row.qty)
		for item_code, qtys in qtys_by_item.items():
			assert sorted(qtys) == [1, 3, 3, 3], f"{item_code}: expected [1, 3, 3, 3], got {sorted(qtys)}"

	finally:
		frappe.delete_doc("Packing Slip", ps.name, force=True)


@pytest.mark.order(118)
def test_auto_cartonize_packing_slip_from_delivery_note_sets_parcels():
	"""
	When Shipstation Settings turns on auto cartonize for packing slips, mapping a
	Delivery Note to a Packing Slip (``get_mapped_doc`` + ``after_mapping``) should
	assign parcel numbers on item lines before save.
	"""
	assert ss_cart.inventory_tools_cartonization_installed(), "inventory_tools must be installed"

	ss = ss_cart.get_enabled_shipstation_cartonization_settings()
	assert ss, "Need enabled Shipstation Settings with cartonization (run before_test first)"
	prev_ps = cint(ss.auto_cartonize_packing_slip)
	prev_sh = cint(ss.auto_cartonize_shipment)
	ss.auto_cartonize_packing_slip = 1
	ss.auto_cartonize_shipment = 0
	ss.save()

	try:
		so_name = get_so_with_items(
			customers[2], "Ambrosia Pie Company", ["Gooseberry Pie", "Kaduka Key Lime Pie"]
		)
		assert so_name, "Sales Order for Cafe 27 Cafeteria must exist (run before_test first)"

		dn_name = frappe.db.get_value(
			"Delivery Note Item", {"against_sales_order": so_name, "docstatus": 0}, "parent"
		)
		assert dn_name, "Delivery Note for Cafe 27 Cafeteria must exist (run before_test first)"

		ps = make_packing_slip(dn_name)
		assert len(ps.items) >= 2, "Mapped Packing Slip should have unpacked item lines"

		for row in ps.items:
			assert (
				row.parcel_number
			), f"Packing Slip Item {row.item_code} should get a parcel number from auto-cartonization"
	finally:
		ss.reload()
		ss.auto_cartonize_packing_slip = prev_ps
		ss.auto_cartonize_shipment = prev_sh
		ss.save()


@pytest.mark.order(119)
def test_auto_cartonize_shipment_document_sets_parcels_on_sdn_rows():
	"""
	``auto_cartonize_shipment_document`` runs after core ``make_shipment`` inside
	the Shipstation whitelisted entry. With auto cartonize for shipments enabled,
	item-level Shipment Delivery Note rows should receive parcel numbers.
	"""
	assert ss_cart.inventory_tools_cartonization_installed(), "inventory_tools must be installed"

	ss = ss_cart.get_enabled_shipstation_cartonization_settings()
	assert ss, "Need enabled Shipstation Settings with cartonization (run before_test first)"
	prev_ps = cint(ss.auto_cartonize_packing_slip)
	prev_sh = cint(ss.auto_cartonize_shipment)
	ss.auto_cartonize_packing_slip = 0
	ss.auto_cartonize_shipment = 1
	ss.save()

	try:
		company = frappe.defaults.get_global_default("default_company") or "Ambrosia Pie Company"
		so_name = get_so_with_items(customers[1], company, ["Ambrosia Pie", "Double Plum Pie"])
		assert so_name, "Small-parcel Sales Order must exist (run before_test first)"

		dn_name = frappe.db.get_value(
			"Delivery Note Item",
			{"against_sales_order": so_name, "docstatus": 0},
			"parent",
		)
		assert dn_name, "Small-parcel Delivery Note must exist (run before_test first)"

		dn = frappe.get_doc("Delivery Note", dn_name)

		sh = frappe.new_doc("Shipment")
		sh.pickup_company = company
		sh.delivery_customer = customers[1]

		for row in dn.items:
			sh.append(
				"shipment_delivery_note",
				{
					"delivery_note": dn.name,
					"dn_detail": row.name,
					"item_code": row.item_code,
					"item_name": row.item_name,
					"qty": row.qty,
					"stock_uom": row.stock_uom,
				},
			)

		ss_cart.auto_cartonize_shipment_document(sh)

		for row in sh.shipment_delivery_note:
			assert (
				row.parcel_number
			), f"Shipment Delivery Note row {row.item_code} should receive a parcel number"

	finally:
		ss.reload()
		ss.auto_cartonize_packing_slip = prev_ps
		ss.auto_cartonize_shipment = prev_sh
		ss.save()
