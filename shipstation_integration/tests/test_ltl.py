# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import pytest
import frappe

from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.tests.setup import get_draft_ltl_shipment_for_tests


@pytest.fixture
def ltl():
	return ShipstationLTL()


@pytest.fixture
def ltl_shipment():
	"""Return the seeded LTL Shipment configured with exactly two SDN rows in parcel 1.

	Setup creates the Shipment with SDN rows across two parcels (one per DN).
	This fixture reduces that to two rows in a single parcel so that individual
	unit tests have a precise, predictable dataset for weight, density, and
	dimension assertions.  The original rows are restored after each test.
	"""
	shipment = get_draft_ltl_shipment_for_tests()
	original_package_type_code = shipment.package_type_code
	original_sdn_rows = [row.as_dict() for row in shipment.shipment_delivery_note]

	# Derive the first DN from the existing SDN rows (avoids hard-coding DN ordering)
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

	yield shipment

	shipment.reload()
	shipment.shipment_delivery_note = []
	for row_data in original_sdn_rows:
		row_data.pop("name", None)
		shipment.append("shipment_delivery_note", row_data)
	shipment.package_type_code = original_package_type_code
	shipment.save()


def test_density_to_freight_class_covers_all_nmfc_thresholds(ltl):
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


def test_density_to_freight_class_above_maximum_is_class_50(ltl):
	assert ltl.density_to_freight_class(200) == 50


def test_density_to_freight_class_just_below_threshold_advances_class(ltl):
	# 50 lb/ft³ → class 50;  49.9 lb/ft³ → class 55
	assert ltl.density_to_freight_class(50) == 50
	assert ltl.density_to_freight_class(49.9) == 55


def test_density_to_freight_class_below_minimum_is_class_500(ltl):
	assert ltl.density_to_freight_class(0.5) == 500


def test_calculate_density_lb_ft3_with_real_shipment_parcel(ltl, ltl_shipment):
	# The test setup creates one shipment_parcel row: 48×40×36 in, 250 lb, count=1
	# → 40 ft³ → 6.25 lb/ft³
	parcel = ltl_shipment.shipment_parcel[0]
	assert abs(ltl.calculate_density_lb_ft3(parcel) - 6.25) < 0.01


def test_calculate_density_lb_ft3_returns_zero_when_weight_is_zero(ltl, ltl_shipment):
	row = ltl_shipment.shipment_parcel[0].as_dict()
	row["weight"] = 0
	assert ltl.calculate_density_lb_ft3(row) == 0.0


def test_calculate_density_lb_ft3_returns_zero_when_a_dimension_is_zero(ltl, ltl_shipment):
	row = ltl_shipment.shipment_parcel[0].as_dict()
	row["length"] = 0
	assert ltl.calculate_density_lb_ft3(row) == 0.0


def test_calculate_density_lb_ft3_returns_zero_when_length_uom_is_missing(ltl, ltl_shipment):
	row = ltl_shipment.shipment_parcel[0].as_dict()
	row["length_uom"] = None
	assert ltl.calculate_density_lb_ft3(row) == 0.0


def test_calculate_density_lb_ft3_count_multiplies_volume(ltl, ltl_shipment):
	row_1 = ltl_shipment.shipment_parcel[0].as_dict()
	row_2 = {**row_1, "count": 2}
	# Doubling volume halves density for the same weight
	assert ltl.calculate_density_lb_ft3(row_2) == pytest.approx(
		ltl.calculate_density_lb_ft3(row_1) / 2, rel=1e-3
	)


def test_validate_billing_passes_with_test_shipment(ltl, ltl_shipment):
	# The test shipment has billing_account="TEST-ACCOUNT-001"
	ltl.validate_billing(ltl_shipment)  # must not raise


def test_validate_billing_raises_when_billing_account_is_empty(ltl, ltl_shipment):
	ltl_shipment.billing_account = ""
	with pytest.raises(frappe.ValidationError):
		ltl.validate_billing(ltl_shipment)


def test_validate_billing_raises_when_billing_account_is_none(ltl, ltl_shipment):
	ltl_shipment.billing_account = None
	with pytest.raises(frappe.ValidationError):
		ltl.validate_billing(ltl_shipment)


def test_build_packages_from_sdn_returns_one_package_per_parcel(ltl, ltl_shipment):
	# All SDN rows are in parcel 1 → exactly one package
	packages = ltl.build_packages_from_sdn(ltl_shipment)
	assert len(packages) == 1


def test_build_packages_from_sdn_package_has_required_keys(ltl, ltl_shipment):
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
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


def test_build_packages_from_sdn_dimensions_match_sdn_rows(ltl, ltl_shipment):
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
	assert pkg["dimensions"]["length"] == 48
	assert pkg["dimensions"]["width"] == 40
	assert pkg["dimensions"]["height"] == 36
	assert pkg["dimensions"]["unit"] == "inches"


def test_build_packages_from_sdn_weight_is_sum_of_row_weights(ltl, ltl_shipment):
	# Two SDN rows × 125 lb each → 250 lb total
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
	assert pkg["weight"]["value"] == 250
	assert pkg["weight"]["unit"] == "pounds"


def test_build_packages_from_sdn_freight_class_derived_from_density(ltl, ltl_shipment):
	# 48×40×36 in = 40 ft³; 250 lb → 6.25 lb/ft³ → NMFC class 150
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
	assert pkg["freight_class"] == 150


def test_build_packages_from_sdn_groups_rows_by_parcel_number(ltl, ltl_shipment):
	# Move the second SDN row to parcel 2 in-memory
	rows = ltl_shipment.shipment_delivery_note
	rows[1].parcel_number = 2
	packages = ltl.build_packages_from_sdn(ltl_shipment)
	assert len(packages) == 2


def test_build_packages_from_sdn_package_type_code_from_shipment(ltl, ltl_shipment):
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
	assert pkg["code"] == "pkg"


def test_build_packages_from_sdn_raises_when_no_rows_have_parcel_number(ltl, ltl_shipment):
	for row in ltl_shipment.shipment_delivery_note:
		row.parcel_number = 0
	with pytest.raises(frappe.ValidationError):
		ltl.build_packages_from_sdn(ltl_shipment)


def test_build_packages_from_sdn_raises_when_parcel_has_no_dimensions(ltl, ltl_shipment):
	for row in ltl_shipment.shipment_delivery_note:
		row.parcel_length = 0
		row.parcel_width = 0
		row.parcel_height = 0
	with pytest.raises(frappe.ValidationError):
		ltl.build_packages_from_sdn(ltl_shipment)


def test_build_packages_from_sdn_raises_when_parcel_has_no_weight(ltl, ltl_shipment):
	for row in ltl_shipment.shipment_delivery_note:
		row.parcel_weight = 0
	with pytest.raises(frappe.ValidationError):
		ltl.build_packages_from_sdn(ltl_shipment)


def test_build_packages_from_sdn_dimensions_from_first_non_zero_row(ltl, ltl_shipment):
	# Clear dims on row 0; row 1 supplies the parcel dimensions
	rows = ltl_shipment.shipment_delivery_note
	rows[0].parcel_length = 0
	rows[0].parcel_width = 0
	rows[0].parcel_height = 0
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
	assert pkg["dimensions"]["length"] == 48  # from row 1


def test_build_packages_from_sdn_nmfc_code_included_when_set(ltl, ltl_shipment):
	ltl_shipment.nmfc_freight_class = "55"
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
	assert pkg.get("nmfc_code") == "55"


def test_build_packages_from_sdn_nmfc_code_omitted_when_not_set(ltl, ltl_shipment):
	ltl_shipment.nmfc_freight_class = None
	pkg = ltl.build_packages_from_sdn(ltl_shipment)[0]
	assert "nmfc_code" not in pkg


def test_resolve_package_type_code_skips_api_when_code_already_set(ltl, ltl_shipment, monkeypatch):
	# The fixture sets package_type_code="PLT" — no API call should be made
	calls = []
	monkeypatch.setattr(ltl, "list_ltl_carrier_package_types", lambda *a, **kw: calls.append(1) or [])
	ltl.resolve_package_type_code(ltl_shipment, "carrier-1")
	assert not calls


def test_resolve_package_type_code_matches_carrier_package_by_name(ltl, ltl_shipment, monkeypatch):
	ltl_shipment.package_type_code = None
	ltl_shipment.package_type = "Pallets"
	pkg_types = [{"name": "Pallets", "code": "PLT"}, {"name": "Crates", "code": "CRT"}]
	monkeypatch.setattr(ltl, "list_ltl_carrier_package_types", lambda *a, **kw: pkg_types)
	ltl.resolve_package_type_code(ltl_shipment, "carrier-1")
	assert ltl_shipment.package_type_code == "PLT"


def test_resolve_package_type_code_match_is_case_insensitive(ltl, ltl_shipment, monkeypatch):
	ltl_shipment.package_type_code = None
	ltl_shipment.package_type = "pallets"
	monkeypatch.setattr(
		ltl,
		"list_ltl_carrier_package_types",
		lambda *a, **kw: [{"name": "Pallets", "code": "PLT"}],
	)
	ltl.resolve_package_type_code(ltl_shipment, "carrier-1")
	assert ltl_shipment.package_type_code == "PLT"


def test_resolve_package_type_code_falls_back_to_first_when_no_match(
	ltl, ltl_shipment, monkeypatch
):
	ltl_shipment.package_type_code = None
	ltl_shipment.package_type = "Unknown"
	pkg_types = [{"name": "Pallets", "code": "PLT"}, {"name": "Crates", "code": "CRT"}]
	monkeypatch.setattr(ltl, "list_ltl_carrier_package_types", lambda *a, **kw: pkg_types)
	ltl.resolve_package_type_code(ltl_shipment, "carrier-1")
	assert ltl_shipment.package_type_code == "PLT"


def test_resolve_package_type_code_raises_when_carrier_returns_no_types(
	ltl, ltl_shipment, monkeypatch
):
	ltl_shipment.package_type_code = None
	monkeypatch.setattr(ltl, "list_ltl_carrier_package_types", lambda *a, **kw: [])
	with pytest.raises(frappe.ValidationError):
		ltl.resolve_package_type_code(ltl_shipment, "carrier-1")


def test_get_shipment_measurements_has_all_required_keys(ltl, ltl_shipment):
	m = ltl.get_shipment_measurements_object_from_doc(ltl_shipment)
	for key in ("total_linear_length", "total_width", "total_height", "total_weight"):
		assert key in m
		assert "value" in m[key]
		assert "unit" in m[key]


def test_get_shipment_measurements_values_from_sdn_rows(ltl, ltl_shipment):
	# Single parcel: 48×40×36 in, 250 lb total
	m = ltl.get_shipment_measurements_object_from_doc(ltl_shipment)
	assert m["total_linear_length"] == {"value": 48, "unit": "inches"}
	assert m["total_width"] == {"value": 40, "unit": "inches"}
	assert m["total_height"] == {"value": 36, "unit": "inches"}
	assert m["total_weight"] == {"value": 250, "unit": "pounds"}


def test_get_shipment_measurements_aggregates_two_parcels(ltl, ltl_shipment):
	# Move row 1 to parcel 2 with smaller dimensions in-memory
	rows = ltl_shipment.shipment_delivery_note
	rows[1].parcel_number = 2
	rows[1].parcel_length = 24
	rows[1].parcel_width = 20
	rows[1].parcel_height = 18

	m = ltl.get_shipment_measurements_object_from_doc(ltl_shipment)
	assert m["total_linear_length"]["value"] == 72  # 48 + 24
	assert m["total_width"]["value"] == 40  # max(40, 20)
	assert m["total_height"]["value"] == 36  # max(36, 18)
	assert m["total_weight"]["value"] == 250  # 125 + 125


def test_get_shipment_measurements_uom_strings_come_from_sdn(ltl, ltl_shipment):
	assert (
		ltl.get_shipment_measurements_object_from_doc(ltl_shipment)["total_linear_length"]["unit"]
		== "inches"
	)
	assert (
		ltl.get_shipment_measurements_object_from_doc(ltl_shipment)["total_weight"]["unit"] == "pounds"
	)
