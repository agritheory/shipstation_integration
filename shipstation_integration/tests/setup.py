# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from pathlib import Path

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.setup.utils import set_defaults_for_tests
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.utils import getdate


def read_json(name):
	"""Read a fixture JSON file from the tests/fixtures directory."""
	fixtures_dir = Path(frappe.get_app_path("shipstation_integration", "tests", "fixtures"))
	return frappe.get_file_json(fixtures_dir / f"{name}.json")


CUSTOMERS = read_json("customers")
ITEMS = read_json("items")


def before_test():
	frappe.clear_cache()
	today = getdate()

	setup_complete(
		{
			"currency": "USD",
			"full_name": "Shipstation User",
			"company_name": "Chelsea Fruit Co",
			"timezone": "America/New_York",
			"company_abbr": "CFC",
			"domains": ["Distribution"],
			"country": "United States",
			"fy_start_date": today.replace(month=1, day=1).isoformat(),
			"fy_end_date": today.replace(month=12, day=31).isoformat(),
			"language": "english",
			"company_tagline": "Chelsea Fruit Co",
			"email": "shipstation@chelseafruit.co",
			"password": "admin",
			"chart_of_accounts": "Standard with Numbers",
			"bank_account": "Primary Checking",
		}
	)

	set_defaults_for_tests()
	frappe.db.commit()

	create_test_data()

	for module in frappe.get_all("Module Onboarding"):
		frappe.db.set_value("Module Onboarding", module, "is_complete", True)
	frappe.db.set_single_value("Website Settings", "home_page", "login")


def create_test_data():
	settings = frappe._dict(
		{
			"day": getdate().replace(month=1, day=1),
			"company": "Chelsea Fruit Co",
		}
	)
	create_customer_group()
	create_price_list()
	create_item_groups()
	create_warehouses(settings)
	create_transporters()
	create_parcel_templates()
	create_items(settings)
	create_customers(settings)
	create_addresses(settings)
	create_shipstation_settings()
	create_sales_order(settings)
	create_delivery_note(settings)
	create_packing_slip(settings)


def create_shipstation_settings():
	"""Create a Shipstation Settings document with a test GS1 company prefix."""
	if frappe.db.exists("Shipstation Settings", "Shipstation Settings"):
		return

	default_item_group = frappe.get_value("Item Group", {"is_group": 0}, "name")

	ss = frappe.new_doc("Shipstation Settings")
	ss.name = "Shipstation Settings"
	ss.enabled = 1
	ss.default_item_group = default_item_group
	ss.gs1_company_prefix = "0614141"
	ss.save()


def create_customer_group():
	if frappe.db.get_value("Customer Group", {"customer_group_name": "ShipStation"}):
		return

	customer_group = frappe.new_doc("Customer Group")
	customer_group.customer_group_name = "ShipStation"
	customer_group.parent_customer_group = "All Customer Groups"
	customer_group.save()


def create_price_list():
	if frappe.db.get_value("Price List", {"price_list_name": "ShipStation"}):
		return

	price_list = frappe.new_doc("Price List")
	price_list.price_list_name = "ShipStation"
	price_list.selling = True
	price_list.save()


def create_item_groups():
	"""Create item groups for the test items."""
	for group_name in ("Baked Goods", "Ingredients", "Bakery Supplies", "Sub Assemblies"):
		if frappe.db.exists("Item Group", group_name):
			continue
		ig = frappe.new_doc("Item Group")
		ig.item_group_name = group_name
		ig.parent_item_group = "All Item Groups"
		ig.save()


def create_warehouses(settings):
	"""Create a basic warehouse for the company."""
	warehouse_name = "Stores - CFC"
	if frappe.db.exists("Warehouse", warehouse_name):
		return

	root_wh = frappe.get_value("Warehouse", {"company": settings.company, "is_group": 1})
	wh = frappe.new_doc("Warehouse")
	wh.warehouse_name = "Stores"
	wh.parent_warehouse = root_wh
	wh.company = settings.company
	wh.save()


def create_transporters():
	"""Create transporter suppliers for FedEx, UPS, and USPS."""
	carriers = ["FedEx", "UPS", "USPS"]
	default_supplier_group = frappe.get_value("Supplier Group", {"is_group": 0}, "name")

	for carrier_name in carriers:
		if frappe.db.exists("Supplier", {"supplier_name": carrier_name, "is_transporter": 1}):
			continue

		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = carrier_name
		supplier.supplier_group = default_supplier_group
		supplier.is_transporter = 1
		supplier.save()


def create_parcel_templates():
	"""Create a standard parcel template for testing."""
	# 12x9x6 inches = 30.48 x 22.86 x 15.24 cm, 3 lbs = 1.36 kg
	template_name = "Small Box"
	if frappe.db.exists("Shipment Parcel Template", template_name):
		return

	template = frappe.new_doc("Shipment Parcel Template")
	template.parcel_template_name = template_name
	template.length = 30.48  # cm (12 inches)
	template.width = 22.86  # cm (9 inches)
	template.height = 15.24  # cm (6 inches)
	template.weight = 1.36  # kg (3 lbs)
	template.package_code = "small_box"
	template.skip_shipstation_sync = 1  # Skip sync for testing
	template.save()


def create_items(settings):
	"""Create test items from the fixture data."""
	for item_data in ITEMS[:10]:  # Create first 10 items for simplicity
		if frappe.db.exists("Item", item_data.get("item_code")):
			continue

		i = frappe.new_doc("Item")
		i.item_code = item_data.get("item_code")
		i.item_name = item_data.get("item_code")
		i.item_group = item_data.get("item_group", "Ingredients")
		i.stock_uom = item_data.get("uom", "Nos")
		i.description = item_data.get("description", "")
		i.is_stock_item = 1
		i.valuation_method = "Moving Average"

		# Set weight if available
		if item_data.get("weight_per_unit"):
			i.weight_per_unit = item_data.get("weight_per_unit")
			i.weight_uom = item_data.get("weight_uom", "Pound")

		# Add item defaults
		i.append(
			"item_defaults",
			{
				"company": settings.company,
				"default_warehouse": "Stores - CFC",
			},
		)

		i.save()


def create_customers(settings):
	"""Create test customers from the fixture data."""
	for customer_name in CUSTOMERS:
		if frappe.db.exists("Customer", customer_name):
			continue

		customer = frappe.new_doc("Customer")
		customer.customer_name = customer_name
		customer.customer_group = "ShipStation"
		customer.customer_type = "Company"
		customer.territory = "United States"
		customer.save()


def create_addresses(settings):
	"""Create ship-to and ship-from addresses."""
	# Company address (ship-from)
	company_address_title = f"{settings.company} - Chelsea"
	if not frappe.db.exists("Address", {"address_title": company_address_title}):
		company_addr = frappe.new_doc("Address")
		company_addr.address_title = company_address_title
		company_addr.address_type = "Office"
		company_addr.address_line1 = "67C Sweeny Street"
		company_addr.city = "Chelsea"
		company_addr.state = "MA"
		company_addr.pincode = "89077"
		company_addr.country = "United States"
		company_addr.phone = "(617) 555-1234"
		company_addr.is_your_company_address = 1
		company_addr.append("links", {"link_doctype": "Company", "link_name": settings.company})
		company_addr.save()

	# Customer address (ship-to) for first customer
	customer_name = CUSTOMERS[0]
	customer_address_title = f"{customer_name} - Portland"
	if not frappe.db.exists("Address", {"address_title": customer_address_title}):
		customer_addr = frappe.new_doc("Address")
		customer_addr.address_title = customer_address_title
		customer_addr.address_type = "Billing"
		customer_addr.address_line1 = "123 Ocean Avenue"
		customer_addr.city = "Portland"
		customer_addr.state = "ME"
		customer_addr.pincode = "04101"
		customer_addr.country = "United States"
		customer_addr.phone = "(207) 555-5678"
		customer_addr.append("links", {"link_doctype": "Customer", "link_name": customer_name})
		customer_addr.save()


def create_sales_order(settings):
	"""Create a draft Sales Order."""
	if frappe.db.exists("Sales Order", {"customer": CUSTOMERS[0], "docstatus": 0}):
		return

	so = frappe.new_doc("Sales Order")
	so.transaction_date = settings.day
	so.customer = CUSTOMERS[0]
	so.order_type = "Sales"
	so.currency = "USD"
	so.company = settings.company
	so.selling_price_list = "ShipStation"

	# Add items with weight
	so.append(
		"items",
		{
			"item_code": "Ambrosia Pie",
			"delivery_date": settings.day,
			"qty": 5,
			"warehouse": "Stores - CFC",
		},
	)
	so.append(
		"items",
		{
			"item_code": "Gooseberry Pie",
			"delivery_date": settings.day,
			"qty": 3,
			"warehouse": "Stores - CFC",
		},
	)

	so.save()
	so.submit()


def create_delivery_note(settings):
	"""Create a draft Delivery Note from the Sales Order."""
	so = frappe.get_last_doc("Sales Order")
	if frappe.db.exists("Delivery Note", {"sales_order": so.name, "docstatus": 0}):
		return

	dn = make_delivery_note(so.name)
	dn.save()


def create_packing_slip(settings):
	"""Create a Packing Slip with shipping fields populated."""
	dn = frappe.get_last_doc("Delivery Note")
	if frappe.db.exists("Packing Slip", {"delivery_note": dn.name}):
		return

	# Create Packing Slip using the ERPNext builtin
	ps = make_packing_slip(dn.name)

	# Get address names by title
	customer_name = CUSTOMERS[0]
	company_address_title = f"{settings.company} - Chelsea"
	customer_address_title = f"{customer_name} - Portland"

	company_address = frappe.get_value("Address", {"address_title": company_address_title}, "name")
	customer_address = frappe.get_value("Address", {"address_title": customer_address_title}, "name")

	# Verify addresses exist
	if not company_address or not customer_address:
		frappe.throw(f"Addresses not found. Company: {company_address}, Customer: {customer_address}")

	# Set shipping fields
	ps.shipping_address_name = customer_address
	ps.dispatch_address_name = company_address
	ps.carrier = "USPS"
	ps.freight_type = "Small Parcel"
	ps.carrier_service = "usps_priority_mail"

	# Assign all items to parcel 1 with test dimensions
	for item_row in ps.items:
		item_row.parcel_number = 1
		item_row.parcel_length = 12
		item_row.parcel_width = 9
		item_row.parcel_height = 6
		item_row.dimension_uom = "Inch"
		item_row.parcel_weight = 3
		item_row.parcel_weight_uom = "Pound"

	ps.save()
