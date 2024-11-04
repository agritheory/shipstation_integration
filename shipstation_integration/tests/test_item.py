# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt


import frappe
import pytest
from shipstation.models import ShipStationOrderItem, ShipStationWeight

from shipstation_integration.items import create_item


@pytest.fixture
def settings():
	settings = frappe.new_doc("Shipstation Settings")
	settings.update(
		{
			"shipstation_user": "Administrator",
			"weight_conversion": "Convert to Gram",
			"default_item_group": "All Item Groups",
		}
	)
	return settings


@pytest.fixture
def item1():
	weight = ShipStationWeight(
		value=1.0,
		units="Ounce",
	)

	return ShipStationOrderItem(
		name="Test Item 1",
		sku="TEST-ITEM-1",
		weight=weight,
	)


@pytest.fixture
def item2():
	weight = ShipStationWeight(
		value=1.0,
		units="Pound",
	)

	return ShipStationOrderItem(
		name="Test Item 2",
		sku="TEST-ITEM-2",
		weight=weight,
	)


@pytest.fixture
def item3():
	weight = ShipStationWeight(
		value=1.0,
		units="Gram",
	)

	return ShipStationOrderItem(
		name="Test Item 3",
		sku="TEST-ITEM-3",
		weight=weight,
	)


def test_create_item(item1, item2, item3, settings):
	item = create_item(item1, settings)
	assert item.is_new() is None
	assert item.item_code == "TEST-ITEM-1"
	assert item.item_name == "Test Item 1"
	assert item.weight_per_unit == 28.35
	assert item.weight_uom == "Gram"

	item = create_item(item2, settings)
	assert item.is_new() is None
	assert item.item_code == "TEST-ITEM-2"
	assert item.item_name == "Test Item 2"
	assert item.weight_per_unit == 453.59
	assert item.weight_uom == "Gram"

	item = create_item(item3, settings)
	assert item.is_new() is None
	assert item.item_code == "TEST-ITEM-3"
	assert item.item_name == "Test Item 3"
	assert item.weight_per_unit == 1
	assert item.weight_uom == "Gram"
