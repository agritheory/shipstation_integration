# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import pytest
import frappe

from shipstation_integration.ltl import ShipstationLTL, get_ltl_provider
from shipstation_integration.shipstation_integration.freight_providers.banyan_ltl import BanyanLTL
from shipstation_integration.shipstation_integration.freight_providers.odfl_ltl import OdflLTL
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
	"""
	Mutate the shipment in-memory and persist it so it has exactly two SDN rows
	in parcel 1 with known 48×40×36 in / 125 lb dimensions.  Returns a dict of
	the original state so the caller can restore it afterward.
	"""
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


###############################################################################
# get_ltl_provider
###############################################################################


def test_get_ltl_provider_returns_shipstation_ltl_when_no_doc_given():
	assert isinstance(get_ltl_provider(), ShipstationLTL)


def test_get_ltl_provider_returns_shipstation_ltl_when_preferred_carrier_is_none():
	doc = frappe._dict(
		pickup_from_type="Company",
		pickup_company="Ambrosia Pie Company",
		preferred_carrier=None,
	)
	assert isinstance(get_ltl_provider(doc), ShipstationLTL)


def test_get_ltl_provider_falls_back_to_shipstation_ltl_when_no_fcs_record_exists():
	assert isinstance(
		get_ltl_provider(doc_for_supplier("Nonexistent Carrier That Has No FCS")), ShipstationLTL
	)


def test_get_ltl_provider_returns_shipstation_ltl_for_shipengine_fcs():
	assert isinstance(
		get_ltl_provider(doc_for_supplier(supplier_name("Test LTL Carrier"))), ShipstationLTL
	)


def test_get_ltl_provider_returns_wwex_ltl_for_wwex_fcs():
	assert isinstance(get_ltl_provider(doc_for_supplier(supplier_name("WWEX LTL"))), WwexLTL)


def test_get_ltl_provider_returns_banyan_ltl_for_banyan_fcs():
	assert isinstance(get_ltl_provider(doc_for_supplier(supplier_name("Banyan LTL"))), BanyanLTL)


def test_get_ltl_provider_returns_odfl_ltl_for_odfl_fcs():
	assert isinstance(get_ltl_provider(doc_for_supplier(supplier_name("ODFL LTL"))), OdflLTL)


###############################################################################
# Freight-class / density — pure computation, no DB mutation needed
###############################################################################


def test_density_to_freight_class_covers_all_nmfc_thresholds():
	ltl = ShipstationLTL()
	expected = [
		(50, 50),
		(35, 55),
		(30, 60),
		(22.5, 65),
		(15, 70),
		(13.5, 77.5),
		(12, 85),
		(10.5, 92.5),
		(9, 100),
		(8, 110),
		(7, 125),
		(6, 150),
		(5, 175),
		(4, 200),
		(3, 250),
		(2, 300),
		(1, 400),
	]
	for density, freight_class in expected:
		result = ltl.density_to_freight_class(density)
		assert (
			result == freight_class
		), f"density {density} lb/ft³ → expected class {freight_class}, got {result}"


def test_density_to_freight_class_above_maximum_is_class_50():
	assert ShipstationLTL().density_to_freight_class(200) == 50


def test_density_to_freight_class_just_below_threshold_advances_class():
	ltl = ShipstationLTL()
	assert ltl.density_to_freight_class(50) == 50
	assert ltl.density_to_freight_class(49.9) == 55


def test_density_to_freight_class_below_minimum_is_class_500():
	assert ShipstationLTL().density_to_freight_class(0.5) == 500


###############################################################################
# Density calculation — reads shipment_parcel from the seeded shipment
###############################################################################


def test_calculate_density_lb_ft3_with_real_shipment_parcel():
	# shipment_parcel[0]: 48×40×36 in, weight=250 lb, count=1 → 40 ft³ → 6.25 lb/ft³
	shipment = get_draft_ltl_shipment_for_tests()
	parcel = shipment.shipment_parcel[0]
	assert abs(ShipstationLTL().calculate_density_lb_ft3(parcel) - 6.25) < 0.01


def test_calculate_density_lb_ft3_returns_zero_when_weight_is_zero():
	shipment = get_draft_ltl_shipment_for_tests()
	row = shipment.shipment_parcel[0].as_dict()
	row["weight"] = 0
	assert ShipstationLTL().calculate_density_lb_ft3(row) == 0.0


def test_calculate_density_lb_ft3_returns_zero_when_a_dimension_is_zero():
	shipment = get_draft_ltl_shipment_for_tests()
	row = shipment.shipment_parcel[0].as_dict()
	row["length"] = 0
	assert ShipstationLTL().calculate_density_lb_ft3(row) == 0.0


def test_calculate_density_lb_ft3_returns_zero_when_length_uom_is_missing():
	shipment = get_draft_ltl_shipment_for_tests()
	row = shipment.shipment_parcel[0].as_dict()
	row["length_uom"] = None
	assert ShipstationLTL().calculate_density_lb_ft3(row) == 0.0


def test_calculate_density_lb_ft3_count_multiplies_volume():
	shipment = get_draft_ltl_shipment_for_tests()
	ltl = ShipstationLTL()
	row_1 = shipment.shipment_parcel[0].as_dict()
	row_2 = {**row_1, "count": 2}
	assert ltl.calculate_density_lb_ft3(row_2) == pytest.approx(
		ltl.calculate_density_lb_ft3(row_1) / 2, rel=1e-3
	)


###############################################################################
# validate_billing
###############################################################################


def test_validate_billing_passes_with_test_shipment():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		ShipstationLTL().validate_billing(shipment)
	finally:
		restore_ltl_shipment(shipment, original)


def test_validate_billing_raises_when_billing_account_is_empty():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		shipment.billing_account = ""
		with pytest.raises(frappe.ValidationError):
			ShipstationLTL().validate_billing(shipment)
	finally:
		restore_ltl_shipment(shipment, original)


def test_validate_billing_raises_when_billing_account_is_none():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		shipment.billing_account = None
		with pytest.raises(frappe.ValidationError):
			ShipstationLTL().validate_billing(shipment)
	finally:
		restore_ltl_shipment(shipment, original)


###############################################################################
# build_packages_from_sdn
###############################################################################


def test_build_packages_from_sdn_returns_one_package_per_parcel():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		assert len(ShipstationLTL().build_packages_from_sdn(shipment)) == 1
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_package_has_required_keys():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		pkg = ShipstationLTL().build_packages_from_sdn(shipment)[0]
		for key in (
			"code",
			"dimensions",
			"weight",
			"freight_class",
			"density",
			"description",
			"quantity",
		):
			assert key in pkg
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_dimensions_match_sdn_rows():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		pkg = ShipstationLTL().build_packages_from_sdn(shipment)[0]
		assert pkg["dimensions"]["length"] == 48
		assert pkg["dimensions"]["width"] == 40
		assert pkg["dimensions"]["height"] == 36
		assert pkg["dimensions"]["unit"] == "inches"
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_weight_is_sum_of_row_weights():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		pkg = ShipstationLTL().build_packages_from_sdn(shipment)[0]
		assert pkg["weight"]["value"] == 250
		assert pkg["weight"]["unit"] == "pounds"
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_freight_class_derived_from_density():
	# 48×40×36 in = 40 ft³; 250 lb → 6.25 lb/ft³ → NMFC class 150
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		assert ShipstationLTL().build_packages_from_sdn(shipment)[0]["freight_class"] == 150
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_groups_rows_by_parcel_number():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		shipment.shipment_delivery_note[1].parcel_number = 2
		assert len(ShipstationLTL().build_packages_from_sdn(shipment)) == 2
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_package_type_code_from_shipment():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		assert ShipstationLTL().build_packages_from_sdn(shipment)[0]["code"] == "pkg"
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_raises_when_no_rows_have_parcel_number():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		for row in shipment.shipment_delivery_note:
			row.parcel_number = 0
		with pytest.raises(frappe.ValidationError):
			ShipstationLTL().build_packages_from_sdn(shipment)
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_raises_when_parcel_has_no_dimensions():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		for row in shipment.shipment_delivery_note:
			row.parcel_length = 0
			row.parcel_width = 0
			row.parcel_height = 0
		with pytest.raises(frappe.ValidationError):
			ShipstationLTL().build_packages_from_sdn(shipment)
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_raises_when_parcel_has_no_weight():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		for row in shipment.shipment_delivery_note:
			row.parcel_weight = 0
		with pytest.raises(frappe.ValidationError):
			ShipstationLTL().build_packages_from_sdn(shipment)
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_dimensions_from_first_non_zero_row():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		rows = shipment.shipment_delivery_note
		rows[0].parcel_length = 0
		rows[0].parcel_width = 0
		rows[0].parcel_height = 0
		pkg = ShipstationLTL().build_packages_from_sdn(shipment)[0]
		assert pkg["dimensions"]["length"] == 48
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_nmfc_code_included_when_set():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		shipment.nmfc_freight_class = "55"
		assert ShipstationLTL().build_packages_from_sdn(shipment)[0].get("nmfc_code") == "55"
	finally:
		restore_ltl_shipment(shipment, original)


def test_build_packages_from_sdn_nmfc_code_omitted_when_not_set():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		shipment.nmfc_freight_class = None
		assert "nmfc_code" not in ShipstationLTL().build_packages_from_sdn(shipment)[0]
	finally:
		restore_ltl_shipment(shipment, original)


###############################################################################
# resolve_package_type_code
###############################################################################


def test_resolve_package_type_code_skips_api_when_code_already_set(monkeypatch):
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		ltl = ShipstationLTL()
		calls = []
		monkeypatch.setattr(
			ltl, "list_ltl_carrier_package_types", lambda *a, **kw: calls.append(1) or []
		)
		ltl.resolve_package_type_code(shipment, "carrier-1")
		assert not calls
	finally:
		restore_ltl_shipment(shipment, original)


def test_resolve_package_type_code_matches_carrier_package_by_name(monkeypatch):
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		ltl = ShipstationLTL()
		shipment.package_type_code = None
		shipment.package_type = "Pallets"
		pkg_types = [{"name": "Pallets", "code": "PLT"}, {"name": "Crates", "code": "CRT"}]
		monkeypatch.setattr(ltl, "list_ltl_carrier_package_types", lambda *a, **kw: pkg_types)
		ltl.resolve_package_type_code(shipment, "carrier-1")
		assert shipment.package_type_code == "PLT"
	finally:
		restore_ltl_shipment(shipment, original)


def test_resolve_package_type_code_match_is_case_insensitive(monkeypatch):
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		ltl = ShipstationLTL()
		shipment.package_type_code = None
		shipment.package_type = "pallets"
		monkeypatch.setattr(
			ltl, "list_ltl_carrier_package_types", lambda *a, **kw: [{"name": "Pallets", "code": "PLT"}]
		)
		ltl.resolve_package_type_code(shipment, "carrier-1")
		assert shipment.package_type_code == "PLT"
	finally:
		restore_ltl_shipment(shipment, original)


def test_resolve_package_type_code_falls_back_to_first_when_no_match(monkeypatch):
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		ltl = ShipstationLTL()
		shipment.package_type_code = None
		shipment.package_type = "Unknown"
		pkg_types = [{"name": "Pallets", "code": "PLT"}, {"name": "Crates", "code": "CRT"}]
		monkeypatch.setattr(ltl, "list_ltl_carrier_package_types", lambda *a, **kw: pkg_types)
		ltl.resolve_package_type_code(shipment, "carrier-1")
		assert shipment.package_type_code == "PLT"
	finally:
		restore_ltl_shipment(shipment, original)


def test_resolve_package_type_code_raises_when_carrier_returns_no_types(monkeypatch):
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		ltl = ShipstationLTL()
		shipment.package_type_code = None
		monkeypatch.setattr(ltl, "list_ltl_carrier_package_types", lambda *a, **kw: [])
		with pytest.raises(frappe.ValidationError):
			ltl.resolve_package_type_code(shipment, "carrier-1")
	finally:
		restore_ltl_shipment(shipment, original)


###############################################################################
# get_shipment_measurements_object_from_doc
###############################################################################


def test_get_shipment_measurements_has_all_required_keys():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		m = ShipstationLTL().get_shipment_measurements_object_from_doc(shipment)
		for key in ("total_linear_length", "total_width", "total_height", "total_weight"):
			assert key in m
			assert "value" in m[key]
			assert "unit" in m[key]
	finally:
		restore_ltl_shipment(shipment, original)


def test_get_shipment_measurements_values_from_sdn_rows():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		m = ShipstationLTL().get_shipment_measurements_object_from_doc(shipment)
		assert m["total_linear_length"] == {"value": 48, "unit": "inches"}
		assert m["total_width"] == {"value": 40, "unit": "inches"}
		assert m["total_height"] == {"value": 36, "unit": "inches"}
		assert m["total_weight"] == {"value": 250, "unit": "pounds"}
	finally:
		restore_ltl_shipment(shipment, original)


def test_get_shipment_measurements_aggregates_two_parcels():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		rows = shipment.shipment_delivery_note
		rows[1].parcel_number = 2
		rows[1].parcel_length = 24
		rows[1].parcel_width = 20
		rows[1].parcel_height = 18
		m = ShipstationLTL().get_shipment_measurements_object_from_doc(shipment)
		assert m["total_linear_length"]["value"] == 72
		assert m["total_width"]["value"] == 40
		assert m["total_height"]["value"] == 36
		assert m["total_weight"]["value"] == 250
	finally:
		restore_ltl_shipment(shipment, original)


def test_get_shipment_measurements_uom_strings_come_from_sdn():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		ltl = ShipstationLTL()
		assert (
			ltl.get_shipment_measurements_object_from_doc(shipment)["total_linear_length"]["unit"]
			== "inches"
		)
		assert (
			ltl.get_shipment_measurements_object_from_doc(shipment)["total_weight"]["unit"] == "pounds"
		)
	finally:
		restore_ltl_shipment(shipment, original)
