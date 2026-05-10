# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import pytest
import frappe
from frappe.utils import flt

from shipstation_integration.ltl import ShipstationLTL, get_ltl_provider
from shipstation_integration.shipstation_integration.freight_providers.banyan_ltl import BanyanLTL
from shipstation_integration.shipstation_integration.freight_providers.odfl_ltl import OdflLTL
from shipstation_integration.shipstation_integration.freight_providers.traffictech_ltl import (
	TrafficTechLTL,
)
from shipstation_integration.shipstation_integration.freight_providers.wwex_ltl import WwexLTL
from shipstation_integration.tests.setup import get_draft_ltl_shipment_for_tests


def supplier_name(carrier_supplier_name):
	return frappe.db.get_value(
		"Supplier", {"supplier_name": carrier_supplier_name, "is_transporter": 1}, "name"
	)


def doc_for_supplier(name):
	return frappe._dict(
		pickup_from_type="Company",
		pickup_company="Ambrosia Pie Company",
		preferred_carrier=name,
	)


def configure_ltl_shipment_for_parcel_tests(shipment):
	original_package_type_code = shipment.package_type_code
	original_sdn_rows = [row.as_dict() for row in shipment.shipment_delivery_note]

	first_dn_name = shipment.shipment_delivery_note[0].delivery_note
	dn_items = frappe.get_all(
		"Delivery Note Item",
		filters={"parent": first_dn_name},
		fields=["name", "item_code", "item_name", "qty", "stock_uom"],
	)

	shipment.shipment_delivery_note = []
	for item in dn_items[:2]:
		shipment.append(
			"shipment_delivery_note",
			{
				"delivery_note": first_dn_name,
				"dn_detail": item.name,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"qty": item.qty,
				"stock_uom": item.stock_uom,
				"parcel_number": 1,
				"parcel_length": 48,
				"parcel_width": 40,
				"parcel_height": 36,
				"dimension_uom": "Inch",
				"parcel_weight": 125,
				"parcel_weight_uom": "Pound",
			},
		)

	shipment.package_type_code = "pkg"
	shipment.save()
	shipment.reload()

	return {"package_type_code": original_package_type_code, "sdn_rows": original_sdn_rows}


def restore_ltl_shipment(shipment, original):
	shipment.reload()
	shipment.shipment_delivery_note = []
	for row_data in original["sdn_rows"]:
		row_data.pop("name", None)
		shipment.append("shipment_delivery_note", row_data)
	shipment.package_type_code = original["package_type_code"]
	shipment.save()


@pytest.mark.order(50)
@pytest.mark.parametrize(
	("density", "expected_class"),
	[
		(200, 50),
		(49.9, 55),
		(0.5, 500),
	],
)
def test_density_to_freight_class_boundaries(density, expected_class):
	assert ShipstationLTL().density_to_freight_class(density) == expected_class


@pytest.mark.order(51)
@pytest.mark.parametrize(
	("case", "expected_cls"),
	[
		("WWEX LTL", WwexLTL),
		("Banyan LTL", BanyanLTL),
		("ODFL LTL", OdflLTL),
		("TrafficTech LTL", TrafficTechLTL),
		("Test LTL Carrier", ShipstationLTL),
		("no_doc", ShipstationLTL),
		("no_carrier", ShipstationLTL),
		("missing_fcs", ShipstationLTL),
	],
)
def test_get_ltl_provider_resolves_by_fcs_type(case, expected_cls):
	if case == "no_doc":
		assert isinstance(get_ltl_provider(), expected_cls)
	elif case == "no_carrier":
		assert isinstance(
			get_ltl_provider(
				frappe._dict(
					pickup_from_type="Company",
					pickup_company="Ambrosia Pie Company",
					preferred_carrier=None,
				)
			),
			expected_cls,
		)
	elif case == "missing_fcs":
		assert isinstance(
			get_ltl_provider(doc_for_supplier("Nonexistent Carrier That Has No FCS")), expected_cls
		)
	else:
		doc = doc_for_supplier(supplier_name(case))
		assert isinstance(get_ltl_provider(doc), expected_cls)


@pytest.mark.order(52)
@pytest.mark.parametrize("problem", ("zero_dimensions", "zero_weight"))
def test_build_packages_raises_on_missing_dimensions(problem):
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	dn_before: list[dict] = []
	items_before: dict[str, float] = {}
	try:
		for row in shipment.shipment_delivery_note:
			if problem == "zero_dimensions":
				row.parcel_length = 0
				row.parcel_width = 0
				row.parcel_height = 0
			else:
				row.parcel_weight = 0

		if problem == "zero_weight":
			for row in shipment.shipment_delivery_note:
				rowvals = frappe.db.get_value(
					"Delivery Note Item",
					row.dn_detail,
					["total_weight", "weight_per_unit"],
					as_dict=True,
				)
				dn_before.append(rowvals or {})
				frappe.db.set_value(
					"Delivery Note Item",
					row.dn_detail,
					{"total_weight": 0, "weight_per_unit": 0},
				)
				wp = flt(frappe.db.get_value("Item", row.item_code, "weight_per_unit"))
				if row.item_code not in items_before:
					items_before[row.item_code] = wp
				frappe.db.set_value("Item", row.item_code, "weight_per_unit", 0)

		with pytest.raises(frappe.ValidationError):
			ShipstationLTL().build_packages_from_sdn(shipment)
	finally:
		if problem == "zero_weight":
			for i, row in enumerate(shipment.shipment_delivery_note):
				if i < len(dn_before) and row.dn_detail:
					prev = dn_before[i]
					frappe.db.set_value(
						"Delivery Note Item",
						row.dn_detail,
						{
							"total_weight": prev.get("total_weight"),
							"weight_per_unit": prev.get("weight_per_unit"),
						},
					)
			for item_code, wp in items_before.items():
				frappe.db.set_value("Item", item_code, "weight_per_unit", wp)
		restore_ltl_shipment(shipment, original)
