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
