# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from pathlib import Path

import frappe
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from erpnext.setup.utils import set_defaults_for_tests
from erpnext.stock.doctype.delivery_note.delivery_note import make_packing_slip
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.utils import getdate

MOCK_API_KEY = "mock_test_api_key_abc123"

TEST_17TRACK_COMPANY = "Chelsea Fruit Co"
SEED_TN_WEBHOOK = "1Z2617V10397725789"
SEED_TN_ONE_REF = "TRK-SEED-0000001"
SEED_TN_TWO_REF = "TRK-SEED-0000002"

# Events extracted from the mock_17track_webhook.json fixture, geocoded via Nominatim.
# TN1 ends Delivered (west coast); TN2 ends InTransit (midwest); TN3 ends OutForDelivery (east coast).
# This spread ensures 3 distinct colored pins are visible on the map in tests.
SEED_TN_WEBHOOK_EVENTS = [
	{
		"event_time": "2022-03-29 05:43:08",
		"stage": "InfoReceived",
		"description": "Shipper created a label, UPS has not received the package yet.",
		"location": "US",
		"country": "US",
		"state": "",
		"city": "",
		"latitude": 39.7837304,
		"longitude": -100.445882,
		"coordinates_source": "Geocoded",
		"provider": "UPS",
	},
	{
		"event_time": "2022-03-31 23:36:47",
		"stage": "",
		"description": "Origin Scan",
		"location": "Ontario, CA, US",
		"country": "US",
		"state": "CA",
		"city": "Ontario",
		"latitude": 34.065846,
		"longitude": -117.64843,
		"coordinates_source": "Geocoded",
		"provider": "UPS",
	},
	{
		"event_time": "2022-04-02 09:15:00",
		"stage": "",
		"description": "Arrived at Facility",
		"location": "Anderson, CA, US",
		"country": "US",
		"state": "CA",
		"city": "Anderson",
		"latitude": 40.4479345,
		"longitude": -122.2982544,
		"coordinates_source": "Geocoded",
		"provider": "UPS",
	},
	{
		"event_time": "2022-04-04 15:46:06",
		"stage": "OutForDelivery",
		"description": "Out For Delivery Today",
		"location": "Crescent City, CA, US",
		"country": "US",
		"state": "CA",
		"city": "Crescent City",
		"latitude": 41.7557501,
		"longitude": -124.2025913,
		"coordinates_source": "Geocoded",
		"provider": "UPS",
	},
	{
		"event_time": "2022-04-04 23:35:22",
		"stage": "Delivered",
		"description": "DELIVERED",
		"location": "GASQUET, CA, US",
		"country": "US",
		"state": "CA",
		"city": "GASQUET",
		"latitude": 41.8399,
		"longitude": -123.9729,
		"coordinates_source": "Geocoded",
		"provider": "UPS",
	},
]


SEED_TN_ONE_REF_EVENTS = [
	{
		"event_time": "2022-04-01 10:00:00",
		"stage": "InfoReceived",
		"description": "Shipment information received.",
		"location": "Chicago, IL, US",
		"country": "US",
		"state": "IL",
		"city": "Chicago",
		"latitude": 41.8781,
		"longitude": -87.6298,
		"coordinates_source": "Geocoded",
		"provider": "FedEx",
	},
	{
		"event_time": "2022-04-03 14:22:00",
		"stage": "InTransit",
		"description": "Package in transit.",
		"location": "Kansas City, MO, US",
		"country": "US",
		"state": "MO",
		"city": "Kansas City",
		"latitude": 39.0997,
		"longitude": -94.5786,
		"coordinates_source": "Geocoded",
		"provider": "FedEx",
	},
]

SEED_TN_TWO_REF_EVENTS = [
	{
		"event_time": "2022-04-05 08:00:00",
		"stage": "InfoReceived",
		"description": "Label created.",
		"location": "Miami, FL, US",
		"country": "US",
		"state": "FL",
		"city": "Miami",
		"latitude": 25.7617,
		"longitude": -80.1918,
		"coordinates_source": "Geocoded",
		"provider": "USPS",
	},
	{
		"event_time": "2022-04-06 11:30:00",
		"stage": "InTransit",
		"description": "Departed facility.",
		"location": "Charlotte, NC, US",
		"country": "US",
		"state": "NC",
		"city": "Charlotte",
		"latitude": 35.2271,
		"longitude": -80.8431,
		"coordinates_source": "Geocoded",
		"provider": "USPS",
	},
	{
		"event_time": "2022-04-07 09:15:00",
		"stage": "OutForDelivery",
		"description": "Out for delivery.",
		"location": "Richmond, VA, US",
		"country": "US",
		"state": "VA",
		"city": "Richmond",
		"latitude": 37.5407,
		"longitude": -77.436,
		"coordinates_source": "Geocoded",
		"provider": "USPS",
	},
]


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
			"company_name": TEST_17TRACK_COMPANY,
			"timezone": "America/New_York",
			"company_abbr": "CFC",
			"domains": ["Distribution"],
			"country": "United States",
			"fy_start_date": today.replace(month=1, day=1).isoformat(),
			"fy_end_date": today.replace(month=12, day=31).isoformat(),
			"language": "english",
			"company_tagline": TEST_17TRACK_COMPANY,
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
			"company": TEST_17TRACK_COMPANY,
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
	create_sales_order(settings)
	create_delivery_note(settings)
	create_packing_slip(settings)
	create_seventeen_track_settings()
	create_test_tracking_numbers()


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

	# Add parcel dimensions
	ps.append(
		"parcel_dimensions",
		{
			"length": 12,
			"width": 9,
			"height": 6,
			"dimension_uom": "Inch",
			"weight": 3,
			"weight_uom": "Pound",
		},
	)

	ps.save()


def create_seventeen_track_settings(company: str = TEST_17TRACK_COMPANY):
	if frappe.db.exists("Seventeen Track", company):
		doc = frappe.get_doc("Seventeen Track", company)
	else:
		doc = frappe.new_doc("Seventeen Track")
		doc.company = company
	if doc.seventeen_track_user and not frappe.db.exists("User", doc.seventeen_track_user):
		doc.seventeen_track_user = None
	doc.add_updates_as_comments = 1
	doc.api_key = MOCK_API_KEY
	doc.flags.ignore_permissions = True
	doc.flags.ignore_validate = True
	doc.save()
	from shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track import (
		build_seventeen_track_webhook_callback_uri,
	)

	webhook_uri = build_seventeen_track_webhook_callback_uri()
	frappe.db.set_value("Seventeen Track", doc.name, "webhook_callback_uri", webhook_uri)
	frappe.db.commit()


def create_test_tracking_numbers():
	"""Create 3 seed Tracking Number records without triggering real 17Track API calls."""
	seed_item_1 = "Ambrosia Pie"
	seed_item_2 = "Double Plum Pie"
	seed_item_3 = "Gooseberry Pie"

	# TN1 — primary webhook test target: submitted, active, with pre-geocoded events for map testing
	if not frappe.db.exists("Tracking Number", {"tracking_number": SEED_TN_WEBHOOK}):
		tn = frappe.get_doc({"doctype": "Tracking Number", "tracking_number": SEED_TN_WEBHOOK})
		tn.flags.ignore_validate = True
		tn.insert(ignore_permissions=True)
		frappe.db.set_value("Tracking Number", tn.name, "docstatus", 1)
		frappe.db.set_value("Tracking Number", tn.name, "subscription_status", "Active")

	tn_name = frappe.db.get_value("Tracking Number", {"tracking_number": SEED_TN_WEBHOOK}, "name")
	if not frappe.db.exists("Tracking Number Event", {"parent": tn_name}):
		tn_doc = frappe.get_doc("Tracking Number", tn_name)
		for event_data in SEED_TN_WEBHOOK_EVENTS:
			row = tn_doc.append("tracking_number_event", event_data)
			row.db_insert()

		events_with_coords = [e for e in SEED_TN_WEBHOOK_EVENTS if e.get("coordinates_source") in ("API", "Geocoded")]
		if events_with_coords:
			latest = max(events_with_coords, key=lambda e: str(e.get("event_time") or ""))
			frappe.db.set_value("Tracking Number", tn_name, "last_latitude", str(latest["latitude"]))
			frappe.db.set_value("Tracking Number", tn_name, "last_longitude", str(latest["longitude"]))
			frappe.db.set_value("Tracking Number", tn_name, "last_event_location", latest.get("location") or "")
		frappe.db.set_value("Tracking Number", tn_name, "seventeen_track_status", "Delivered")

		frappe.db.commit()

	# TN2 — single reference to an Item, ends InTransit (midwest)
	if not frappe.db.exists("Tracking Number", {"tracking_number": SEED_TN_ONE_REF}):
		tn = frappe.get_doc(
			{
				"doctype": "Tracking Number",
				"tracking_number": SEED_TN_ONE_REF,
				"references": [{"reference_doctype": "Item", "document_name": seed_item_3}],
			}
		)
		tn.flags.ignore_validate = True
		tn.insert(ignore_permissions=True)
		frappe.db.set_value("Tracking Number", tn.name, "docstatus", 1)
		frappe.db.set_value("Tracking Number", tn.name, "subscription_status", "Active")

	tn2_name = frappe.db.get_value("Tracking Number", {"tracking_number": SEED_TN_ONE_REF}, "name")
	if not frappe.db.exists("Tracking Number Event", {"parent": tn2_name}):
		tn_doc = frappe.get_doc("Tracking Number", tn2_name)
		for event_data in SEED_TN_ONE_REF_EVENTS:
			row = tn_doc.append("tracking_number_event", event_data)
			row.db_insert()

		events_with_coords = [e for e in SEED_TN_ONE_REF_EVENTS if e.get("coordinates_source") in ("API", "Geocoded")]
		if events_with_coords:
			latest = max(events_with_coords, key=lambda e: str(e.get("event_time") or ""))
			frappe.db.set_value("Tracking Number", tn2_name, "last_latitude", str(latest["latitude"]))
			frappe.db.set_value("Tracking Number", tn2_name, "last_longitude", str(latest["longitude"]))
			frappe.db.set_value("Tracking Number", tn2_name, "last_event_location", latest.get("location") or "")
		frappe.db.set_value("Tracking Number", tn2_name, "seventeen_track_status", "InTransit")

		frappe.db.commit()

	# TN3 — two references to Items, ends OutForDelivery (east coast)
	if not frappe.db.exists("Tracking Number", {"tracking_number": SEED_TN_TWO_REF}):
		tn = frappe.get_doc(
			{
				"doctype": "Tracking Number",
				"tracking_number": SEED_TN_TWO_REF,
				"references": [
					{"reference_doctype": "Item", "document_name": seed_item_1},
					{"reference_doctype": "Item", "document_name": seed_item_2},
				],
			}
		)
		tn.flags.ignore_validate = True
		tn.insert(ignore_permissions=True)
		frappe.db.set_value("Tracking Number", tn.name, "docstatus", 1)
		frappe.db.set_value("Tracking Number", tn.name, "subscription_status", "Active")

	tn3_name = frappe.db.get_value("Tracking Number", {"tracking_number": SEED_TN_TWO_REF}, "name")
	if not frappe.db.exists("Tracking Number Event", {"parent": tn3_name}):
		tn_doc = frappe.get_doc("Tracking Number", tn3_name)
		for event_data in SEED_TN_TWO_REF_EVENTS:
			row = tn_doc.append("tracking_number_event", event_data)
			row.db_insert()

		events_with_coords = [e for e in SEED_TN_TWO_REF_EVENTS if e.get("coordinates_source") in ("API", "Geocoded")]
		if events_with_coords:
			latest = max(events_with_coords, key=lambda e: str(e.get("event_time") or ""))
			frappe.db.set_value("Tracking Number", tn3_name, "last_latitude", str(latest["latitude"]))
			frappe.db.set_value("Tracking Number", tn3_name, "last_longitude", str(latest["longitude"]))
			frappe.db.set_value("Tracking Number", tn3_name, "last_event_location", latest.get("location") or "")
		frappe.db.set_value("Tracking Number", tn3_name, "seventeen_track_status", "OutForDelivery")

		frappe.db.commit()
