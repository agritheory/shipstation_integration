# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import pytest

from shipstation_integration.parcel_uom_conversion import (
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
