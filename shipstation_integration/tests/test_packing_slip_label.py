# Copyright (c) 2024, AgriTheory and Contributors
# See license.txt

import json
from pathlib import Path

import frappe
import pytest

from shipstation_integration.api.labels import (
	billing_options_for_incoterm,
	build_shipment_from_packing_slip,
	create_label_for_packing_slip,
	is_po_box,
	label_billing_context,
	resolve_label_billing_for_source,
	resolve_label_billing_options,
	validate_po_box_delivery,
	void_label_for_packing_slip,
)
from shipstation_integration.api.rates import (
	carrier_family,
	dedupe_rates,
	get_package_from_packing_slip,
	get_packages_from_packing_slip,
	get_rates_for_packing_slip,
	mark_matching_rate,
)
from shipstation_integration.utils import find_matching_parcel_template
from shipstation_integration.tests.setup import get_small_parcel_packing_slip_for_tests
from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
	get_first_sales_order_from_packing_slip,
)


def get_label_response():
	fixtures_dir = Path(frappe.get_app_path("shipstation_integration", "tests", "fixtures"))
	fixture_file = fixtures_dir / "label_response.json"
	with open(fixture_file) as f:
		fixtures = json.load(f)
	return fixtures["captured_responses"][0]["response"]


def packing_slip_snapshot(ps):
	return {
		"shipping_address_name": ps.shipping_address_name,
		"dispatch_address_name": ps.dispatch_address_name,
		"carrier": ps.carrier,
		"carrier_service": ps.carrier_service,
		"gross_weight_pkg": ps.gross_weight_pkg,
		"items": [
			{
				"name": row.name,
				"parcel_number": row.parcel_number,
				"parcel_length": row.parcel_length,
				"parcel_width": row.parcel_width,
				"parcel_height": row.parcel_height,
				"dimension_uom": row.dimension_uom,
				"parcel_weight": row.parcel_weight,
				"parcel_weight_uom": row.parcel_weight_uom,
				"tracking_number": row.tracking_number,
				"tracking_url": row.tracking_url,
				"label_url": row.label_url,
				"label_id": row.get("label_id"),
				"ucc128": row.ucc128,
			}
			for row in ps.items
		],
	}


def get_packing_slip():
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()

	original = packing_slip_snapshot(ps)

	for row in ps.items:
		row.parcel_number = 1
		row.parcel_length = 12
		row.parcel_width = 9
		row.parcel_height = 6
		row.dimension_uom = "Inch"
		row.parcel_weight = 3
		row.parcel_weight_uom = "Pound"
	ps.save()
	ps.reload()

	return ps, original


def restore_packing_slip(ps, original):
	ps.reload()
	ps.shipping_address_name = original["shipping_address_name"]
	ps.dispatch_address_name = original["dispatch_address_name"]
	ps.carrier = original["carrier"]
	ps.carrier_service = original["carrier_service"]
	ps.gross_weight_pkg = original.get("gross_weight_pkg")
	for saved, row in zip(original["items"], ps.items):
		for field, value in saved.items():
			if field != "name":
				setattr(row, field, value)
	ps.save()


def add_tracking_to_packing_slip(ps):
	row = ps.items[0]
	frappe.db.set_value(
		"Packing Slip Item",
		row.name,
		{
			"tracking_number": "9400111899223100009999",
			"tracking_url": "https://tools.usps.com/go/TrackConfirmAction?tLabels=9400111899223100009999",
			"label_url": "https://api.shipengine.com/v1/downloads/label-pdf-existing",
			"label_id": "se-test-label-existing",
			"carrier": "USPS",
		},
	)
	ps.reload()


def clear_tracking_from_packing_slip(ps):
	row = ps.items[0]
	frappe.db.set_value(
		"Packing Slip Item",
		row.name,
		{
			"tracking_number": None,
			"tracking_url": None,
			"label_url": None,
			"label_id": None,
			"carrier": None,
		},
	)
	ps.reload()


def get_multi_parcel_packing_slip():
	ps = frappe.get_last_doc("Packing Slip", {"docstatus": 0})
	ps.reload()
	return ps, packing_slip_snapshot(ps)


def add_shipping_account_with_number(customer_name, carrier, carrier_service, account_number):
	customer = frappe.get_doc("Customer", customer_name)
	customer.append(
		"shipping_accounts",
		{
			"carrier": carrier,
			"carrier_service": carrier_service,
			"shipping_account_number": account_number,
			"enabled": 1,
			"default": 1,
		},
	)
	customer.save()


def save_customer_shipping_accounts(customer_name):
	customer = frappe.get_doc("Customer", customer_name)
	return [row.as_dict() for row in customer.shipping_accounts]


def restore_customer_shipping_accounts(customer_name, rows):
	customer = frappe.get_doc("Customer", customer_name)
	customer.set("shipping_accounts", rows)
	customer.save()


@pytest.mark.order(80)
def test_create_label_happy_path(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_shipment.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(
			ps.name, carrier_id="se-123", service_code="usps_priority_mail"
		)
		assert results[0]["tracking_number"] == "9400111899223100001234"
		assert results[0]["label_id"] == "se-test-label-123"
		assert results[0]["carrier_code"] == "usps"

		ps.reload()
		item = ps.items[0]
		assert item.tracking_url
		assert "usps" in item.tracking_url.lower()
		assert "9400111899223100001234" in item.tracking_url
		assert item.label_id == "se-test-label-123"
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(81)
@pytest.mark.parametrize(
	"mutate",
	("clear_shipping", "clear_dispatch", "no_carrier"),
)
def test_create_label_validation_errors(mutate):
	ps, original = get_packing_slip()
	try:
		if mutate == "clear_shipping":
			ps.shipping_address_name = None
			ps.save()
		elif mutate == "clear_dispatch":
			ps.dispatch_address_name = None
			ps.save()

		with pytest.raises(frappe.ValidationError):
			if mutate == "no_carrier":
				create_label_for_packing_slip(ps.name)
			else:
				create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(82)
def test_get_rates_for_packing_slip(shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		rates_response = {
			"rates": [
				{
					"rate_id": "se-rate-1",
					"carrier_id": "se-usps",
					"carrier_code": "usps",
					"carrier_friendly_name": "USPS",
					"service_code": "usps_priority_mail",
					"service_type": "Priority Mail",
					"shipping_amount": {"currency": "usd", "amount": 7.95},
					"delivery_days": 1,
					"trackable": True,
				}
			]
		}
		api_client = shipstation_api_client_mock
		api_client.get_rates_from_shipment.return_value = rates_response

		rates = get_rates_for_packing_slip(ps.name)
		assert len(rates) > 0
		assert rates[0]["rate_id"] == "se-rate-1"
		assert rates[0]["carrier_code"] == "usps"
		assert rates[0].get("selected") is True
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(83)
def test_existing_label_blocks_unless_forced(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		add_tracking_to_packing_slip(ps)
		with pytest.raises(frappe.DuplicateEntryError):
			create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")

		label_response = get_label_response()
		api_client = shipstation_api_client_mock
		api_client.create_label_from_shipment.return_value = label_response

		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(
			ps.name, carrier_id="se-123", service_code="usps_priority_mail", force=True
		)

		assert results[0]["tracking_number"] == "9400111899223100001234"
		assert results[0]["label_id"] == "se-test-label-123"
		api_client.create_label_from_shipment.assert_called_once()
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


@pytest.mark.order(92)
@pytest.mark.parametrize("missing", ("weight", "length", "gross_fallback"))
def test_package_requires_weight_and_dimensions(missing):
	ps, original = get_multi_parcel_packing_slip()
	try:
		if missing == "gross_fallback":
			for row in ps.items:
				row.parcel_number = None
			ps.gross_weight_pkg = 10
			ps.save()
			ps.reload()
			assert get_package_from_packing_slip(ps) is None
			return

		row = next(item for item in ps.items if item.parcel_number == 1)
		if missing == "weight":
			row.parcel_weight = 0
			match = "missing weight"
		else:
			row.parcel_length = 0
			match = "missing dimensions"
		ps.save()
		ps.reload()
		with pytest.raises(frappe.ValidationError, match=match):
			get_package_from_packing_slip(ps, parcel_number=1)
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(93)
def test_package_uses_requested_parcel_not_first():
	ps, original = get_multi_parcel_packing_slip()
	try:
		row_two = next(item for item in ps.items if item.parcel_number == 2)
		row_two.parcel_weight = 5
		row_two.parcel_length = 10
		row_two.parcel_width = 8
		row_two.parcel_height = 6
		ps.save()
		ps.reload()

		package_two = get_package_from_packing_slip(ps, parcel_number=2)
		assert package_two["weight"]["value"] == 5
		assert package_two["dimensions"]["length"] == 10

		package_one = get_package_from_packing_slip(ps, parcel_number=1)
		assert package_one["weight"]["value"] == 3
		assert package_one["dimensions"]["length"] == 12
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(94)
def test_shipment_payload_billing_follows_incoterm():
	from beam.tests.fixtures import customers

	ps, original = get_packing_slip()
	account_rows = save_customer_shipping_accounts(customers[1])
	try:
		shipment = build_shipment_from_packing_slip(ps, "se-123", "usps_priority_mail", 1)
		assert "advanced_options" not in shipment

		add_shipping_account_with_number(customers[1], "USPS", "Priority Mail", "BD-COLLECT-441")
		assert billing_options_for_incoterm(ps, "DAP") is None
		assert billing_options_for_incoterm(ps, None) is None

		exw = billing_options_for_incoterm(ps, "EXW")
		assert exw["bill_to_party"] == "third_party"
		assert exw["bill_to_account"] == "BD-COLLECT-441"
	finally:
		restore_customer_shipping_accounts(customers[1], account_rows)
		restore_packing_slip(ps, original)


@pytest.mark.order(95)
def test_label_reference_defaults_to_sales_order_name():
	ps, original = get_packing_slip()
	try:
		so_name = get_first_sales_order_from_packing_slip(ps)
		assert so_name
		shipment = build_shipment_from_packing_slip(ps, "se-123", "usps_priority_mail", 1)
		assert shipment["external_order_id"] == so_name
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(96)
def test_create_label_persists_label_id_on_item(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	existing_tracking_numbers = set(frappe.get_all("Tracking Number", pluck="name"))
	try:
		label_response = get_label_response()
		shipstation_api_client_mock.create_label_from_shipment.return_value = label_response
		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
		ps.reload()
		assert ps.items[0].label_id == "se-test-label-123"
		assert ps.items[0].tracking_number == "9400111899223100001234"
		assert set(frappe.get_all("Tracking Number", pluck="name")) == existing_tracking_numbers
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


@pytest.mark.order(97)
def test_void_label_clears_item_tracking(monkeypatch, shipstation_api_client_mock):
	ps, original = get_packing_slip()
	try:
		label_response = get_label_response()
		shipstation_api_client_mock.create_label_from_shipment.return_value = label_response
		shipstation_api_client_mock.void_label.return_value = {"approved": True, "message": "Voided"}
		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		create_label_for_packing_slip(ps.name, carrier_id="se-123", service_code="usps_priority_mail")
		result = void_label_for_packing_slip(ps.name, 1)
		assert result["approved"] is True
		shipstation_api_client_mock.void_label.assert_called_once_with(label_id="se-test-label-123")

		ps.reload()
		assert not ps.items[0].tracking_number
		assert not ps.items[0].label_url
		assert not ps.items[0].label_id
	finally:
		clear_tracking_from_packing_slip(ps)
		restore_packing_slip(ps, original)


@pytest.mark.order(98)
def test_void_label_requires_label_id_on_parcel():
	ps, original = get_packing_slip()
	try:
		with pytest.raises(frappe.ValidationError, match="no label"):
			void_label_for_packing_slip(ps.name, 1)
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(99)
def test_mark_matching_rate_selects_only_exact_single_match():
	priority = {
		"rate_id": "se-rate-1",
		"service_code": "usps_priority_mail",
		"service_type": "Priority Mail",
	}
	ground = {
		"rate_id": "se-rate-2",
		"service_code": "usps_ground",
		"service_type": "Ground",
	}
	duplicate = {
		"rate_id": "se-rate-3",
		"service_code": "usps_priority_mail",
		"service_type": "Priority Mail",
	}

	one = mark_matching_rate([priority, ground], "usps_priority_mail")
	assert one[0].get("selected") is True
	assert "selected" not in one[1]

	none = mark_matching_rate([priority, ground], "fedex_home_delivery")
	assert all("selected" not in rate for rate in none)

	two = mark_matching_rate([priority, duplicate], "usps_priority_mail")
	assert all("selected" not in rate for rate in two)


@pytest.mark.order(104)
def test_rates_for_packing_slip_sends_all_parcels(shipstation_api_client_mock):
	ps = get_small_parcel_packing_slip_for_tests()
	original = packing_slip_snapshot(ps)
	try:
		api_client = shipstation_api_client_mock
		api_client.get_rates_from_shipment.return_value = {
			"rates": [
				{
					"rate_id": "se-rate-1",
					"carrier_id": "se-usps",
					"carrier_code": "usps",
					"carrier_friendly_name": "USPS",
					"service_code": "usps_priority_mail",
					"service_type": "Priority Mail",
					"shipping_amount": {"currency": "usd", "amount": 7.95},
				}
			]
		}

		get_rates_for_packing_slip(ps.name)
		request = api_client.get_rates_from_shipment.call_args[0][0]
		assert len(request["shipment"]["packages"]) == len(get_packages_from_packing_slip(ps))
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(105)
def test_dedupe_rates_keeps_cheapest_per_carrier_family():
	cheaper = {
		"carrier_code": "fedex",
		"service_type": "FedEx Ground",
		"service_code": "fedex_ground",
		"shipping_amount": {"amount": 10.0},
	}
	duplicate = {
		"carrier_code": "fedex_walleted",
		"service_type": "FedEx Ground",
		"service_code": "fedex_ground_walleted",
		"shipping_amount": {"amount": 12.0},
	}
	result = dedupe_rates([duplicate, cheaper])
	assert len(result) == 1
	assert result[0]["shipping_amount"]["amount"] == 10.0
	assert carrier_family(result[0]) == "fedex"

	selected = mark_matching_rate(result, "fedex_ground")
	assert selected[0].get("selected") is True


@pytest.mark.order(106)
def test_multi_parcel_buy_splits_packages(monkeypatch, shipstation_api_client_mock):
	ps = get_small_parcel_packing_slip_for_tests()
	original = packing_slip_snapshot(ps)
	capabilities_cache_key = "shipstation_carrier_capabilities::se-123"
	try:
		frappe.cache().delete_value(capabilities_cache_key)
		multi_response = {
			"label_id": "se-multi-parent",
			"carrier_code": "fedex",
			"packages": [
				{
					"label_id": "se-test-label-parcel-1",
					"tracking_number": "111111111111",
					"label_download": {"pdf": "https://example.com/label-1.pdf"},
				},
				{
					"label_id": "se-test-label-parcel-2",
					"tracking_number": "222222222222",
					"label_download": {"pdf": "https://example.com/label-2.pdf"},
				},
			],
		}
		shipstation_api_client_mock.create_label_from_shipment.return_value = multi_response
		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(
			ps.name, carrier_id="se-123", service_code="fedex_ground"
		)
		assert len(results) == 2
		shipstation_api_client_mock.create_label_from_shipment.assert_called_once()

		ps.reload()
		parcel_one = [row.label_id for row in ps.items if row.parcel_number == 1]
		parcel_two = [row.label_id for row in ps.items if row.parcel_number == 2]
		assert "se-test-label-parcel-1" in parcel_one
		assert "se-test-label-parcel-2" in parcel_two
	finally:
		frappe.cache().delete_value(capabilities_cache_key)
		clear_tracking_from_packing_slip(ps)
		for row in ps.items:
			if row.parcel_number == 2:
				frappe.db.set_value(
					"Packing Slip Item",
					row.name,
					{
						"tracking_number": None,
						"tracking_url": None,
						"label_url": None,
						"label_id": None,
					},
				)
		restore_packing_slip(ps, original)


@pytest.mark.order(107)
def test_multi_parcel_falls_back_when_carrier_lacks_capability(
	monkeypatch, shipstation_api_client_mock
):
	ps = get_small_parcel_packing_slip_for_tests()
	original = packing_slip_snapshot(ps)
	capabilities_cache_key = "shipstation_carrier_capabilities::se-123"
	try:
		frappe.cache().set_value(
			capabilities_cache_key,
			{"has_multi_package_supporting_services": False},
			expires_in_sec=3600,
		)
		single_response = get_label_response()
		shipstation_api_client_mock.create_label_from_shipment.return_value = single_response
		monkeypatch.setattr(
			"shipstation_integration.api.labels.download_and_attach_label", lambda *a, **kw: None
		)

		results = create_label_for_packing_slip(
			ps.name, carrier_id="se-123", service_code="fedex_ground"
		)
		assert len(results) == 2
		assert shipstation_api_client_mock.create_label_from_shipment.call_count == 2
	finally:
		frappe.cache().delete_value(capabilities_cache_key)
		clear_tracking_from_packing_slip(ps)
		for row in ps.items:
			if row.parcel_number == 2:
				frappe.db.set_value(
					"Packing Slip Item",
					row.name,
					{
						"tracking_number": None,
						"tracking_url": None,
						"label_url": None,
						"label_id": None,
					},
				)
		restore_packing_slip(ps, original)


@pytest.mark.order(108)
def test_find_matching_parcel_template_skips_mismatched_width():
	"""Length and height can match while width still disqualifies the template."""
	template_name = "Narrow Width Test Box"
	if frappe.db.exists("Shipment Parcel Template", template_name):
		frappe.delete_doc("Shipment Parcel Template", template_name, force=True)

	# 13×9×6 in — not the seeded Small Box (12×9×6 in).
	parcel_length_in = 13
	parcel_width_in = 9
	parcel_height_in = 6

	doc = frappe.new_doc("Shipment Parcel Template")
	doc.parcel_template_name = template_name
	doc.length = parcel_length_in * 2.54
	doc.width = 2.54
	doc.height = parcel_height_in * 2.54
	doc.weight = 1
	doc.insert(ignore_permissions=True)
	try:
		assert (
			find_matching_parcel_template(parcel_length_in, parcel_width_in, parcel_height_in, "Inch")
			is None
		)
	finally:
		frappe.delete_doc("Shipment Parcel Template", template_name, force=True)


@pytest.mark.order(114)
def test_delivery_note_billing_uses_label_billing_hooks():
	ps, original = get_packing_slip()
	try:
		ctx, _so_name = label_billing_context(ps.delivery_note, "se-123", ps.items)
		assert resolve_label_billing_for_source(
			ps.delivery_note, "se-123", ps.items
		) == resolve_label_billing_options(ctx)
	finally:
		restore_packing_slip(ps, original)


@pytest.mark.order(115)
def test_is_po_box_recognizes_common_spellings():
	assert is_po_box(frappe._dict(address_line1="P.O. Box 999", address_line2=""))
	assert is_po_box(frappe._dict(address_line1="123 Main St", address_line2="Post Office Box 4"))
	assert not is_po_box(frappe._dict(address_line1="123 Main St", address_line2="Suite 200"))


@pytest.mark.order(116)
def test_validate_po_box_blocks_non_postal_carrier():
	address = frappe._dict(address_line1="PO Box 123", address_line2="")
	with pytest.raises(frappe.ValidationError, match="PO box"):
		validate_po_box_delivery(address, "ups_ground", "UPS")


@pytest.mark.order(117)
def test_validate_po_box_allows_usps():
	address = frappe._dict(address_line1="PO Box 123", address_line2="")
	validate_po_box_delivery(address, "usps_priority_mail", "USPS")


@pytest.mark.order(118)
def test_exw_billing_warns_when_account_missing():
	ps, original = get_packing_slip()
	original_carrier = ps.carrier
	so_name = get_first_sales_order_from_packing_slip(ps)
	original_so_incoterm = (
		frappe.db.get_value("Sales Order", so_name, "incoterm") if so_name else None
	)
	try:
		frappe.clear_messages()
		if so_name:
			frappe.db.set_value("Sales Order", so_name, "incoterm", "EXW")
		ps.carrier = "USPS"
		ps.save()
		options = resolve_label_billing_options(ps)
		assert options is None
		assert any(
			(getattr(message, "title", None) or "") == "Freight Billed to Shipper"
			for message in frappe.message_log
		)
	finally:
		frappe.clear_messages()
		if so_name:
			frappe.db.set_value("Sales Order", so_name, "incoterm", original_so_incoterm)
		ps.reload()
		ps.carrier = original_carrier
		ps.save()
		restore_packing_slip(ps, original)


@pytest.mark.order(109)
def test_dedupe_then_mark_matching_rate_selects_single_service():
	rates = dedupe_rates(
		[
			{
				"carrier_code": "usps",
				"service_code": "usps_priority_mail",
				"service_type": "Priority Mail",
				"shipping_amount": {"amount": 8.0},
			},
			{
				"carrier_code": "usps",
				"service_code": "usps_ground",
				"service_type": "Ground Advantage",
				"shipping_amount": {"amount": 6.0},
			},
		]
	)
	selected = mark_matching_rate(rates, "Priority Mail")
	matches = [rate for rate in selected if rate.get("selected")]
	assert len(matches) == 1
	assert matches[0]["service_type"] == "Priority Mail"
