# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import pytest

from shipstation_integration.parcel_uom_conversion import (
	format_parcel_details,
	parcel_uom_factor,
	parcel_uom_factor_cached,
)


@pytest.fixture(autouse=True)
def clear_parcel_uom_factor_cache():
	parcel_uom_factor_cached.cache_clear()
	yield
	parcel_uom_factor_cached.cache_clear()


def test_parcel_centimeter_to_inch_chained_via_meter():
	cm_to_in = parcel_uom_factor("Centimeter", "Inch")
	assert abs(cm_to_in - (1 / 2.54)) < 1e-5


def test_parcel_kilogram_alias_to_pound():
	k_to_lb = parcel_uom_factor("Kilogram", "Pound")
	assert k_to_lb > 1.9


def test_parcel_kg_to_pound_matches_alias():
	a = parcel_uom_factor("Kg", "Pound")
	b = parcel_uom_factor("Kilogram", "Pound")
	assert abs(a - b) < 1e-9


def test_format_parcel_details_centimeters_and_kg():
	user = "Administrator"
	old_dim = frappe.db.get_value("User", user, "dimension_uom")
	old_wt = frappe.db.get_value("User", user, "weight_uom")
	row = {
		"parcel_template": "Pie Triple Stack",
		"parcel_length": 10,
		"parcel_width": 20,
		"parcel_height": 30,
		"parcel_weight": 2,
		"dimension_uom": "Centimeter",
		"parcel_weight_uom": "Kg",
	}
	try:
		frappe.db.set_value("User", user, {"dimension_uom": "Centimeter", "weight_uom": "Kg"})
		frappe.clear_cache(doctype="User")
		parcel_uom_factor_cached.cache_clear()
		text = format_parcel_details(row, user=user)
		assert "10x20x30cm" in text
		assert "2.0kg" in text
	finally:
		frappe.db.set_value(
			"User",
			user,
			{"dimension_uom": old_dim, "weight_uom": old_wt},
		)
		frappe.clear_cache(doctype="User")
		parcel_uom_factor_cached.cache_clear()


def test_format_parcel_details_requires_parcel_context():
	row = {
		"parcel_length": 12,
		"parcel_width": 9,
		"parcel_height": 6,
		"parcel_weight": 3,
	}
	assert format_parcel_details(row, user="Administrator") == ""


def test_format_parcel_details_requires_template_not_parcel_number():
	row = {
		"parcel_number": 2,
		"parcel_length": 12,
		"parcel_width": 9,
		"parcel_height": 6,
		"parcel_weight": 3,
	}
	assert format_parcel_details(row, user="Administrator") == ""
