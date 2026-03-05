# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.setup.utils import enable_all_roles_and_domains, set_defaults_for_tests
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.utils import getdate

from beam.tests.fixtures import customers


def before_test():
	frappe.clear_cache()
	today = getdate()

	setup_complete(
		{
			"currency": "USD",
			"full_name": "Administrator",
			"company_name": "Ambrosia Pie Company",
			"timezone": "America/New_York",
			"company_abbr": "APC",
			"domains": ["Distribution"],
			"country": "United States",
			"fy_start_date": today.replace(month=1, day=1).isoformat(),
			"fy_end_date": today.replace(month=12, day=31).isoformat(),
			"language": "english",
			"company_tagline": "Ambrosia Pie Company",
			"email": "support@agritheory.dev",
			"password": "admin",
			"chart_of_accounts": "Standard with Numbers",
			"bank_account": "Primary Checking",
		}
	)

	enable_all_roles_and_domains()
	set_defaults_for_tests()
	frappe.db.commit()

	from beam.tests.setup import create_test_data as beam_create_test_data

	beam_create_test_data()

	create_test_data()

	for modu in frappe.get_all("Module Onboarding"):
		frappe.db.set_value("Module Onboarding", modu, "is_complete", 1)
	frappe.db.set_single_value("Website Settings", "home_page", "login")


def create_test_data():
	settings = frappe._dict(
		{
			"day": getdate().replace(month=1, day=1),
			"company": "Ambrosia Pie Company",
		}
	)
	create_transporters()
	create_parcel_templates()
	create_shipstation_settings(settings)
	create_customer_address(settings)
	create_inventory_with_handling_units(settings)
	create_sales_order(settings)
	create_delivery_note(settings)
	create_packing_slip(settings)


def create_inventory_with_handling_units(settings):
	"""
	Create a Material Receipt that seeds stock for the beam integration tests.

	Item quantities and rates mirror the real fixture used during development
	(MAT-STE-2026-00002-1) so that the test environment closely represents
	production.  BEAM's ``generate_handling_units`` hook (``before_submit``)
	assigns a UUID HU to each row automatically when ``enable_handling_units``
	is True; tests may then use these HUs as source stock for Repack SEs.
	"""
	warehouse = "Baked Goods - APC"
	seed_items = [
		{"item_code": "Ambrosia Pie", "qty": 500, "basic_rate": 10.20},
		{"item_code": "Double Plum Pie", "qty": 500, "basic_rate": 9.18},
		{"item_code": "Gooseberry Pie", "qty": 100, "basic_rate": 14.84},
		{"item_code": "Kaduka Key Lime Pie", "qty": 100, "basic_rate": 9.18},
	]

	for entry in seed_items:
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Receipt"
		se.purpose = "Material Receipt"
		se.company = settings.company
		se.posting_date = settings.day
		se.append(
			"items",
			{
				"item_code": entry["item_code"],
				"qty": entry["qty"],
				"t_warehouse": warehouse,
				"basic_rate": entry["basic_rate"],
			},
		)
		se.save()
		se.submit()


def create_shipstation_settings(settings):
	"""Upsert a Shipstation Settings document with the test GS1 company prefix.

	Always sets ``enabled`` and ``gs1_company_prefix`` so that a record
	surviving from a previous test run (possibly without the prefix) does not
	cause ``get_sscc_settings()`` to throw.
	"""
	default_item_group = frappe.get_value("Item Group", {"is_group": 0}, "name")

	try:
		ss = frappe.get_doc("Shipstation Settings", "Shipstation Settings")
	except frappe.DoesNotExistError:
		ss = frappe.new_doc("Shipstation Settings")
		ss.name = settings.company

	ss.enabled = 1
	ss.default_item_group = default_item_group
	ss.gs1_company_prefix = "0614141"
	ss.save()


def create_transporters():
	"""Create transporter suppliers for FedEx, UPS, and USPS."""
	default_supplier_group = frappe.get_value("Supplier Group", {"is_group": 0}, "name")

	for carrier_name in ("FedEx", "UPS", "USPS"):
		if frappe.db.exists("Supplier", {"supplier_name": carrier_name, "is_transporter": 1}):
			continue

		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = carrier_name
		supplier.supplier_group = default_supplier_group
		supplier.is_transporter = 1
		supplier.save()


def create_parcel_templates():
	"""Create a standard parcel template for testing."""
	# 12×9×6 inches → 30.48×22.86×15.24 cm; 3 lbs → 1.36 kg
	if frappe.db.exists("Shipment Parcel Template", "Small Box"):
		return

	template = frappe.new_doc("Shipment Parcel Template")
	template.parcel_template_name = "Small Box"
	template.length = 30.48
	template.width = 22.86
	template.height = 15.24
	template.weight = 1.36
	template.package_code = "small_box"
	template.skip_shipstation_sync = 1
	template.save()


def create_customer_address(settings):
	"""Create a ship-to address for the primary test customer.

	The company (ship-from) address is already created by BEAM's setup.
	"""
	customer_name = customers[0]
	address_title = f"{customer_name} - Portland"
	if frappe.db.exists("Address", {"address_title": address_title}):
		return

	addr = frappe.new_doc("Address")
	addr.address_title = address_title
	addr.address_type = "Billing"
	addr.address_line1 = "123 Ocean Avenue"
	addr.city = "Portland"
	addr.state = "ME"
	addr.pincode = "04101"
	addr.country = "United States"
	addr.phone = "(207) 555-5678"
	addr.append("links", {"link_doctype": "Customer", "link_name": customer_name})
	addr.save()


def create_sales_order(settings):
	"""Create a small Sales Order for packing-slip tests."""
	if frappe.db.exists(
		"Sales Order",
		{"customer": customers[0], "company": settings.company, "status": "To Deliver and Bill"},
	):
		return

	so = frappe.new_doc("Sales Order")
	so.transaction_date = settings.day
	so.customer = customers[0]
	so.order_type = "Sales"
	so.currency = "USD"
	so.company = settings.company
	so.selling_price_list = "Bakery Wholesale"

	so.append(
		"items",
		{
			"item_code": "Ambrosia Pie",
			"delivery_date": settings.day,
			"qty": 5,
			"warehouse": "Baked Goods - APC",
		},
	)
	so.append(
		"items",
		{
			"item_code": "Gooseberry Pie",
			"delivery_date": settings.day,
			"qty": 3,
			"warehouse": "Baked Goods - APC",
		},
	)

	so.save()
	so.submit()


def create_delivery_note(settings):
	"""Create a draft Delivery Note from the packing-slip Sales Order."""
	so = frappe.get_last_doc(
		"Sales Order", filters={"company": settings.company, "customer": customers[0]}
	)
	if frappe.db.exists("Delivery Note", {"against_sales_order": so.name, "docstatus": 0}):
		return

	dn = make_delivery_note(so.name)
	dn.save()


def create_packing_slip(settings):
	"""Create a draft Packing Slip with addresses and carrier pre-filled, items unpacked."""
	dn = frappe.get_last_doc(
		"Delivery Note",
		filters={"company": settings.company, "customer": customers[0]},
	)
	if frappe.db.exists("Packing Slip", {"delivery_note": dn.name}):
		return

	ps = make_packing_slip(dn.name)

	# Ship-to: customer address created above.
	customer_address = frappe.get_value(
		"Address",
		{"address_title": f"{customers[0]} - Portland"},
		"name",
	)
	# Ship-from: company address created by BEAM's setup.
	company_address = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Company", "link_name": settings.company, "parenttype": "Address"},
		"parent",
	)

	if not company_address or not customer_address:
		frappe.throw(f"Addresses not found. Company: {company_address}, Customer: {customer_address}")

	ps.shipping_address_name = customer_address
	ps.dispatch_address_name = company_address
	ps.carrier = "USPS"
	ps.freight_type = "Small Parcel"
	ps.carrier_service = "usps_priority_mail"

	# Assign all items to parcel 1 with small-box dimensions so that
	# label / rate tests can call get_package_from_packing_slip() and
	# build_shipment_from_packing_slip() without extra setup.
	for item in ps.items:
		item.parcel_number = 1
		item.parcel_length = 12
		item.parcel_width = 9
		item.parcel_height = 6
		item.dimension_uom = "Inch"
		item.parcel_weight = 3
		item.parcel_weight_uom = "Pound"

	ps.save()
