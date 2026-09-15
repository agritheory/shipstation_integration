# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import pytest
import frappe
from frappe.utils import flt, get_time

from shipstation_integration.incoterms import carrier_billing_from_incoterm
from shipstation_integration.ltl import (
	LTL_SUPPORTED_DIMENSION_UOMS,
	ShipstationLTL,
	centimeter_values_are_millimeter_magnitudes,
	effective_sdn_dimension_uom,
	get_ltl_provider,
	normalize_freight_class,
	sdn_dimensions_to_inches,
)
from shipstation_integration.shipstation_integration.overrides.shipment import (
	ShipStationShipment,
	get_carrier_id_for_supplier,
)
from shipstation_integration.shipstation_integration.freight_providers.banyan_ltl import BanyanLTL
from shipstation_integration.shipstation_integration.freight_providers.odfl_ltl import OdflLTL
from shipstation_integration.shipstation_integration.freight_providers.traffictech_ltl import (
	TrafficTechLTL,
)
from shipstation_integration.shipstation_integration.freight_providers.wwex_ltl import WwexLTL
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	ltl_pickup_response_for_tests,
	ltl_quotes_response_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


class MockHttpxResponse:
	def __init__(self, json_data=None, status_code=200):
		self._json = json_data
		self.status_code = status_code
		self.text = ""
		self.request = None

	def json(self):
		return self._json

	def raise_for_status(self):
		if self.status_code >= 400:
			raise Exception(f"HTTP {self.status_code}")


class MockHttpxClient:
	def __init__(self, responses):
		self.responses = list(responses)
		self.index = 0

	def __enter__(self):
		return self

	def __exit__(self, *args):
		pass

	def next_response(self):
		resp = self.responses[self.index % len(self.responses)]
		self.index += 1
		return resp

	def get(self, *args, **kwargs):
		return self.next_response()

	def post(self, *args, **kwargs):
		return self.next_response()


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
	if shipment.docstatus == 0:
		shipment.save()
		shipment.reload()

	return {"package_type_code": original_package_type_code, "sdn_rows": original_sdn_rows}


def restore_ltl_shipment(shipment, original):
	if shipment.docstatus == 1:
		# Parcel tests only mutate the in-memory doc; submitted SDN rows stay in the DB.
		shipment.reload()
		return
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
@pytest.mark.parametrize(
	("carrier", "expected"),
	[
		("ODFL LTL", "ODFL"),
		("Banyan LTL", None),
		("WWEX LTL", None),
		("TrafficTech LTL", None),
	],
)
def test_get_carrier_id_for_supplier_uses_preferred_carrier_provider(carrier, expected):
	"""Form load has no Shipment; provider must still resolve from the transporter."""
	assert (
		get_carrier_id_for_supplier(supplier_name(carrier), company="Ambrosia Pie Company") == expected
	)


@pytest.mark.order(52)
def test_api_ltl_carrier_data_tolerates_undeclared_field():
	doc = frappe.get_doc("Shipstation Settings", "Ambrosia Pie Company")
	assert isinstance(doc.api_ltl_carrier_data(), list)


@pytest.mark.order(53)
def test_build_packages_uses_parcel_weight_once_per_parcel():
	"""parcel_weight is copied to every SDN line during packing; quote weight must not multiply it."""
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	items_before: dict[str, float] = {}
	try:
		for row in shipment.shipment_delivery_note:
			frappe.db.set_value(
				"Delivery Note Item",
				row.dn_detail,
				{"total_weight": 0, "weight_per_unit": 0},
			)
			wp = flt(frappe.db.get_value("Item", row.item_code, "weight_per_unit"))
			if row.item_code not in items_before:
				items_before[row.item_code] = wp
			frappe.db.set_value("Item", row.item_code, "weight_per_unit", 0)

		packages = ShipstationLTL().build_packages_from_sdn(shipment)
		assert len(packages) == 1
		assert packages[0]["weight"]["value"] == 125
		assert packages[0]["weight"]["unit"] == "pounds"
	finally:
		for row in shipment.shipment_delivery_note:
			frappe.db.set_value(
				"Delivery Note Item",
				row.dn_detail,
				{"total_weight": 0, "weight_per_unit": 0},
			)
		for item_code, wp in items_before.items():
			frappe.db.set_value("Item", item_code, "weight_per_unit", wp)
		restore_ltl_shipment(shipment, original)


@pytest.mark.order(54)
def test_get_accessorial_service_fields_degrades_on_shipengine_error(monkeypatch):
	doc = frappe._dict(
		preferred_carrier=supplier_name("Test LTL Carrier"),
		carrier_id="aa5d80c5-31db-40d2-b046-3450880e8b2e",
		pickup_from_type="Company",
		pickup_company="Ambrosia Pie Company",
	)
	ltl = ShipstationLTL()
	error_response = MockHttpxResponse(
		json_data={"errors": [{"error_type": "security", "message": "Access denied."}]},
		status_code=401,
	)
	monkeypatch.setattr(
		"httpx.Client",
		lambda: MockHttpxClient([error_response]),
	)
	monkeypatch.setattr(
		ShipstationLTL,
		"get_base_url_and_headers",
		lambda self, doc: ("https://api.shipengine.com", {"Api-Key": "test"}),
	)

	result = ltl.get_accessorial_service_fields(doc)
	assert result["supported"] == []
	assert result["unsupported"] == list(ltl.accessorial_services_map.keys())


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


@pytest.mark.order(53)
def test_shipstation_schedule_ltl_pickup_via_api(monkeypatch):
	reset_ltl_shipment_quotation_test_state()
	shipment = get_draft_ltl_shipment_for_tests()
	assert shipment.docstatus == 1
	pickup_fixture = ltl_pickup_response_for_tests()
	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		shipment, ltl_quotes_response_for_tests()[:1]
	)

	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": shipment.name})
	sq.submit()
	shipment.reload()

	carrier_response = MockHttpxResponse(
		json_data={
			"carrier_id": "aa5d80c5-31db-40d2-b046-3450880e8b2e",
			"scac": "TEST",
			"features": ["scheduled_pickup"],
			"packages": [],
		}
	)
	pickup_response = MockHttpxResponse(json_data=pickup_fixture)
	shared_client = MockHttpxClient([carrier_response, pickup_response])
	monkeypatch.setattr(
		"httpx.Client",
		lambda: shared_client,
	)

	msg = ltl.schedule_ltl_pickup(shipment)

	shipment.reload()
	assert shipment.get("pickup_id") == pickup_fixture["pickup_id"]
	assert shipment.get("awb_number") == pickup_fixture["pro_number"]
	assert shipment.get("shipment_id") == pickup_fixture["shipment_id"]

	attachments = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Shipment", "attached_to_name": shipment.name},
		fields=["file_name"],
	)
	assert len(attachments) >= 1
	assert any("bill_of_lading" in (a.file_name or "").lower() for a in attachments)
	assert pickup_fixture["pickup_id"] in msg
	assert pickup_fixture["pro_number"] in msg


@pytest.mark.order(51)
def test_sdn_dimensions_to_inches_centimeter():
	length, width, height = sdn_dimensions_to_inches(101.6, 121.9, 152.4, "Centimeter")
	assert abs(length - 40) < 0.5
	assert abs(width - 48) < 0.5
	assert abs(height - 60) < 0.5


@pytest.mark.order(51)
def test_sdn_dimensions_to_inches_millimeter():
	length, width, height = sdn_dimensions_to_inches(1016, 1219.2, 1524, "Millimeter")
	assert abs(length - 40) < 0.5
	assert abs(width - 48) < 0.5
	assert abs(height - 60) < 0.5


@pytest.mark.order(51)
def test_centimeter_values_are_millimeter_magnitudes_pallet_pattern():
	assert centimeter_values_are_millimeter_magnitudes(1016, 1219.2, 1524)
	assert not centimeter_values_are_millimeter_magnitudes(101.6, 121.9, 152.4)
	assert not centimeter_values_are_millimeter_magnitudes(48, 40, 36)


@pytest.mark.order(51)
def test_effective_sdn_dimension_uom_keeps_centimeter_label_for_pallet_specs():
	assert effective_sdn_dimension_uom(1016, 1219.2, 1524, "Centimeter") == "Millimeter"
	assert effective_sdn_dimension_uom(101.6, 121.9, 152.4, "Centimeter") == "Centimeter"


@pytest.mark.order(52)
def test_build_packages_converts_pallet_cm_field_values_to_inches():
	"""1016×1219×1524 with Dimension UOM Centimeter → ~40×48×60 in for carriers."""
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		for row in shipment.shipment_delivery_note:
			row.parcel_length = 1016
			row.parcel_width = 1219.2
			row.parcel_height = 1524
			row.dimension_uom = "Centimeter"
		packages = ShipstationLTL().build_packages_from_sdn(shipment)
		dims = packages[0]["dimensions"]
		assert dims["unit"] == "inches"
		assert abs(dims["length"] - 40) < 1
		assert abs(dims["width"] - 48) < 1
		assert abs(dims["height"] - 60) < 1
	finally:
		restore_ltl_shipment(shipment, original)
	for cls in (OdflLTL, WwexLTL, BanyanLTL, TrafficTechLTL):
		uoms = cls().get_shipment_dimension_uoms()["length_uom"]
		assert "Millimeter" in uoms
		assert uoms == LTL_SUPPORTED_DIMENSION_UOMS


@pytest.mark.order(51)
def test_ltl_carriers_expose_millimeter_dimension_uom():
	shipment = get_draft_ltl_shipment_for_tests()
	original = configure_ltl_shipment_for_parcel_tests(shipment)
	try:
		for row in shipment.shipment_delivery_note:
			row.parcel_length = 1016
			row.parcel_width = 1219.2
			row.parcel_height = 1524
			row.dimension_uom = "Millimeter"
		packages = ShipstationLTL().build_packages_from_sdn(shipment)
		dims = packages[0]["dimensions"]
		assert dims["unit"] == "inches"
		assert abs(dims["length"] - 40) < 1
		assert abs(dims["width"] - 48) < 1
		assert abs(dims["height"] - 60) < 1
	finally:
		restore_ltl_shipment(shipment, original)


@pytest.mark.order(64)
@pytest.mark.parametrize(
	("value", "expected"),
	[
		(77.5, "77.5"),
		(50.0, "50"),
		("garbage", "50"),
	],
)
def test_normalize_freight_class(value, expected):
	assert normalize_freight_class(value) == expected


@pytest.mark.order(65)
def test_wwex_splits_overweight_handling_units():
	provider = WwexLTL()
	packages = [
		{
			"weight": {"value": 6000, "unit": "pounds"},
			"dimensions": {"length": 40, "width": 48, "height": 60, "unit": "inches"},
			"quantity": 1,
			"freight_class": 70,
			"description": "Heavy freight",
			"code": "PLT",
		}
	]
	units = provider.build_handling_units(frappe._dict({"hazardous_material": 0}), packages)
	assert units[0]["quantity"] == 2
	per_item_lb = float(units[0]["shippedItemList"][0]["weight"]["value"])
	assert 2900 <= per_item_lb <= 3100


@pytest.mark.order(66)
def test_get_address_and_contact_info_requires_pickup_address():
	shipment = get_draft_ltl_shipment_for_tests()
	original_pickup = shipment.pickup_address_name
	try:
		shipment.pickup_address_name = None
		with pytest.raises(frappe.ValidationError, match="pickup"):
			ShipstationLTL().get_address_and_contact_info(shipment, ship_from=True)
	finally:
		shipment.pickup_address_name = original_pickup


@pytest.mark.order(68)
def test_carrier_billing_from_incoterm_keeps_fields_separate():
	exw = carrier_billing_from_incoterm("EXW")
	assert exw["payment_terms"] == "Collect"
	assert exw["billing_type"] == "Consignee"

	dap = carrier_billing_from_incoterm("DAP")
	assert dap["payment_terms"] == "Prepaid"
	assert dap["billing_type"] == "Shipper"


@pytest.mark.order(69)
def test_populate_shipment_incoterm_from_linked_delivery_note():
	shipment = get_draft_ltl_shipment_for_tests()
	delivery_note = next(
		row.delivery_note for row in shipment.shipment_delivery_note if row.delivery_note
	)
	original_dn_incoterm = frappe.db.get_value("Delivery Note", delivery_note, "incoterm")
	original_shipment_incoterm = shipment.incoterm
	try:
		frappe.db.set_value("Delivery Note", delivery_note, "incoterm", "FCA")
		frappe.db.set_value("Shipment", shipment.name, "incoterm", None)
		shipment.reload()
		shipment.incoterm = None
		doc = ShipStationShipment(shipment.as_dict())
		doc.validate()
		assert doc.incoterm == "FCA"
	finally:
		frappe.db.set_value("Delivery Note", delivery_note, "incoterm", original_dn_incoterm)
		frappe.db.set_value("Shipment", shipment.name, "incoterm", original_shipment_incoterm)


@pytest.mark.order(67)
def test_normalize_pickup_window_restores_default_hours():
	shipment = get_draft_ltl_shipment_for_tests()
	original_from = shipment.pickup_from
	original_to = shipment.pickup_to
	try:
		shipment.freight_type = "LTL"
		shipment.pickup_from = shipment.pickup_to = "12:00:00"
		doc = ShipStationShipment(shipment.as_dict())
		doc.validate()
		assert get_time(doc.pickup_from) == get_time("09:00:00")
		assert get_time(doc.pickup_to) == get_time("17:00:00")
	finally:
		shipment.pickup_from = original_from
		shipment.pickup_to = original_to
		shipment.save()
