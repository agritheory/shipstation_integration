# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import json
from pathlib import Path

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
	create_freight_item(settings)
	create_freight_clearing_account(settings)
	create_shipstation_settings(settings)
	ensure_administrator_has_phone()
	create_freight_carrier_settings_for_tests(settings)
	create_customer_addresses_and_contacts(settings)
	create_inventory_with_handling_units(settings)
	create_sales_orders(settings)
	create_delivery_notes(settings)
	create_packing_slips(settings)
	create_shipments(settings)


# ──────────────────────────────────────────────────────────────────────────────
# Infrastructure
# ──────────────────────────────────────────────────────────────────────────────


def create_inventory_with_handling_units(settings):
	"""Seed stock for all test items in the Baked Goods warehouse."""
	warehouse = "Baked Goods - APC"
	seed_items = [
		{"item_code": "Ambrosia Pie", "qty": 500, "basic_rate": 10.20},
		{"item_code": "Double Plum Pie", "qty": 500, "basic_rate": 9.18},
		{"item_code": "Gooseberry Pie", "qty": 500, "basic_rate": 14.84},
		{"item_code": "Kaduka Key Lime Pie", "qty": 500, "basic_rate": 9.18},
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


def ensure_administrator_has_phone():
	user = frappe.get_doc("User", "Administrator")
	if not user.phone:
		user.phone = "(508) 555-0100"
		user.save(ignore_permissions=True)


def create_shipstation_settings(settings):
	"""Upsert a Shipstation Settings document with GS1 company prefix and carrier data."""
	default_item_group = frappe.get_value("Item Group", {"is_group": 0}, "name")

	if frappe.db.exists("Shipstation Settings", settings.company):
		ss = frappe.get_doc("Shipstation Settings", settings.company)
	else:
		ss = frappe.new_doc("Shipstation Settings")
		ss.name = settings.company

	ss.enabled = 1
	ss.enable_shipstation_api = 1
	ss.default_item_group = default_item_group
	ss.gs1_company_prefix = "0614141"
	ss.shipstation_user = "Administrator"
	ss.set("shipstation_api_key", "test_shipstation_api_key_for_ci")
	ss.shipstation_api_carrier_data = json.dumps(
		[
			{"carrier_id": "se-123", "carrier_code": "usps"},
			{"carrier_id": "se-456", "carrier_code": "fedex"},
			{"carrier_id": "se-789", "carrier_code": "ups"},
		]
	)
	ss.save()


def create_freight_item(settings):
	"""Create an Item that represents freight expense on Purchase Invoices."""
	if frappe.db.exists("Item", "Freight Service"):
		return

	freight_expense_account = frappe.db.get_value(
		"Account",
		{"account_name": "Freight and Forwarding Charges", "company": settings.company},
		"name",
	) or frappe.db.get_value(
		"Account",
		{"root_type": "Expense", "is_group": 0, "company": settings.company},
		"name",
	)

	item = frappe.new_doc("Item")
	item.item_code = "Freight Service"
	item.item_name = "Freight Service"
	item.item_group = frappe.get_value("Item Group", {"is_group": 0}, "name")
	item.stock_uom = "Nos"
	item.is_stock_item = 0
	item.is_purchase_item = 1
	item.is_sales_item = 1
	item.description = "Freight and carrier charges"
	if freight_expense_account:
		item.append(
			"item_defaults",
			{
				"company": settings.company,
				"expense_account": freight_expense_account,
			},
		)
	item.save()


def create_freight_clearing_account(settings):
	"""Create Freight Clearing and Freight Receivable accounts for freight accounting.

	Freight Clearing: Asset account used for Collect billing (no party required)
	Freight Receivable: Receivable account used for Prepaid chargeback (with customer party)
	"""
	parent_asset = frappe.db.get_value(
		"Account",
		{"account_name": "Current Assets", "company": settings.company, "is_group": 1},
		"name",
	)
	if not parent_asset:
		parent_asset = frappe.db.get_value(
			"Account",
			{"root_type": "Asset", "is_group": 1, "company": settings.company},
			"name",
		)

	# Create Freight Clearing (no party requirement) for Collect billing
	if not frappe.db.exists(
		"Account", {"account_name": "Freight Clearing", "company": settings.company}
	):
		account = frappe.new_doc("Account")
		account.account_name = "Freight Clearing"
		account.company = settings.company
		account.parent_account = parent_asset
		account.account_type = ""  # Leave blank - it's a clearing account
		account.root_type = "Asset"
		account.is_group = 0
		account.save()

	# Create Freight Receivable (Receivable type) for Prepaid chargeback with customer
	if not frappe.db.exists(
		"Account", {"account_name": "Freight Receivable", "company": settings.company}
	):
		parent_receivable = frappe.db.get_value(
			"Account",
			{"account_name": "Accounts Receivable", "company": settings.company, "is_group": 1},
			"name",
		)
		if not parent_receivable:
			parent_receivable = frappe.db.get_value(
				"Account",
				{"root_type": "Asset", "is_group": 1, "company": settings.company},
				"name",
			)
		account = frappe.new_doc("Account")
		account.account_name = "Freight Receivable"
		account.company = settings.company
		account.parent_account = parent_receivable
		account.account_type = "Receivable"
		account.root_type = "Asset"
		account.is_group = 0
		account.save()


def create_freight_carrier_settings_for_tests(settings):
	"""Seed Freight Carrier Settings with API credentials and freight item."""
	supplier = frappe.db.get_value(
		"Supplier", {"supplier_name": "Test LTL Carrier", "is_transporter": 1}, "name"
	)
	if not supplier:
		return

	existing = frappe.db.get_value(
		"Freight Carrier Settings",
		{"company": settings.company, "supplier": supplier},
		"name",
	)
	if existing:
		fc = frappe.get_doc("Freight Carrier Settings", existing)
	else:
		fc = frappe.new_doc("Freight Carrier Settings")
		fc.company = settings.company
		fc.supplier = supplier
		fc.insert(ignore_permissions=True)
		fc.reload()

	fc.set("ltl_api_key", "test_ltl_api_key_for_ci")
	fc.auto_create_accounting_entry = 1
	if not (fc.base_url or "").strip():
		fc.base_url = "https://api.shipengine.com"
	if not fc.freight_item and frappe.db.exists("Item", "Freight Service"):
		fc.freight_item = "Freight Service"
	if not fc.freight_expense_account:
		fc.freight_expense_account = frappe.db.get_value(
			"Account",
			{"account_name": "Freight and Forwarding Charges", "company": settings.company},
			"name",
		)
	if not fc.freight_clearing_account:
		fc.freight_clearing_account = frappe.db.get_value(
			"Account",
			{"account_name": "Freight Clearing", "company": settings.company},
			"name",
		)
	fc.save(ignore_permissions=True)

	if frappe.db.exists("Shipstation Settings", settings.company):
		ss = frappe.get_doc("Shipstation Settings", settings.company)
		ss.ltl_fetch_freight_carrier_settings = fc.name
		ss.save()

	# Create FCS for multi-provider LTL carriers
	ltl_carriers = [
		{"name": "ShipStation LTL", "base_url": "https://api.shipengine.com", "scac": "SHIP"},
		{"name": "WWEX LTL", "base_url": "https://speedship.staging-wwex.com", "scac": "WWEX"},
		{
			"name": "Banyan LTL",
			"base_url": "https://ws.integration.banyantechnology.com",
			"scac": "BYAN",
		},
		{"name": "ODFL LTL", "base_url": "https://api.odfl.com", "scac": "ODFL"},
	]

	for carrier in ltl_carriers:
		supplier = frappe.db.get_value(
			"Supplier", {"supplier_name": carrier["name"], "is_transporter": 1}, "name"
		)
		if not supplier:
			continue

		existing = frappe.db.get_value(
			"Freight Carrier Settings",
			{"company": settings.company, "supplier": supplier},
			"name",
		)
		if existing:
			fc = frappe.get_doc("Freight Carrier Settings", existing)
		else:
			fc = frappe.new_doc("Freight Carrier Settings")
			fc.company = settings.company
			fc.supplier = supplier
			fc.base_url = carrier["base_url"]
			fc.insert(ignore_permissions=True)
			fc.reload()

		fc.set("ltl_api_key", "test_ltl_api_key_for_ci")
		if carrier["name"] == "ODFL LTL":
			fc.client_id = "test_odfl_client_id"
			fc.set("client_secret", "test_odfl_client_secret")
		fc.auto_create_accounting_entry = 1
		fc.base_url = carrier["base_url"]
		if not (fc.ltl_carrier_scac or "").strip():
			fc.ltl_carrier_scac = carrier["scac"]
		if not fc.freight_item and frappe.db.exists("Item", "Freight Service"):
			fc.freight_item = "Freight Service"
		if not fc.freight_expense_account:
			fc.freight_expense_account = frappe.db.get_value(
				"Account",
				{"account_name": "Freight and Forwarding Charges", "company": settings.company},
				"name",
			)
		if not fc.freight_clearing_account:
			fc.freight_clearing_account = frappe.db.get_value(
				"Account",
				{"account_name": "Freight Clearing", "company": settings.company},
				"name",
			)
		fc.save(ignore_permissions=True)


def create_transporters():
	"""Create transporter suppliers for FedEx, UPS, USPS, test LTL carrier, and four multi-provider LTL carriers."""
	default_supplier_group = frappe.get_value("Supplier Group", {"is_group": 0}, "name")

	for carrier_name in ("FedEx", "UPS", "USPS"):
		if frappe.db.exists("Supplier", {"supplier_name": carrier_name, "is_transporter": 1}):
			continue
		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = carrier_name
		supplier.supplier_group = default_supplier_group
		supplier.is_transporter = 1
		supplier.save()

	if not frappe.db.exists("Supplier", {"supplier_name": "Test LTL Carrier", "is_transporter": 1}):
		ltl = frappe.new_doc("Supplier")
		ltl.supplier_name = "Test LTL Carrier"
		ltl.supplier_group = default_supplier_group
		ltl.is_transporter = 1
		ltl.save()
		frappe.db.set_value(
			"Supplier",
			ltl.name,
			{
				"ltl_carrier_id": "aa5d80c5-31db-40d2-b046-3450880e8b2e",
				"ltl_carrier_scac": "TEST",
			},
		)

	# Create multi-provider LTL carriers
	ltl_carriers = [
		{"name": "ShipStation LTL", "scac": "SHIP", "carrier_id": "se-shipstation-ltl"},
		{"name": "WWEX LTL", "scac": "WWEX", "carrier_id": "wwex-carrier"},
		{"name": "Banyan LTL", "scac": "BYAN", "carrier_id": "banyan-carrier"},
		{"name": "ODFL LTL", "scac": "ODFL", "carrier_id": "odfl-carrier"},
	]

	for carrier in ltl_carriers:
		if frappe.db.exists("Supplier", {"supplier_name": carrier["name"], "is_transporter": 1}):
			continue
		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = carrier["name"]
		supplier.supplier_group = default_supplier_group
		supplier.is_transporter = 1
		supplier.save()
		frappe.db.set_value(
			"Supplier",
			supplier.name,
			{
				"ltl_carrier_id": carrier["carrier_id"],
				"ltl_carrier_scac": carrier["scac"],
			},
		)


def create_parcel_templates():
	"""Create parcel templates for small-parcel and pallet tests."""
	templates = [
		{
			"parcel_template_name": "Small Box",
			"length": 30.48,  # 12 in
			"width": 22.86,  # 9 in
			"height": 15.24,  # 6 in
			"weight": 1.36,
			"package_code": "small_box",
		},
		{
			"parcel_template_name": "Medium Box",
			"length": 45.72,  # 18 in
			"width": 35.56,  # 14 in
			"height": 30.48,  # 12 in
			"weight": 4.54,
			"package_code": "medium_box",
		},
	]
	for tmpl in templates:
		if frappe.db.exists("Shipment Parcel Template", tmpl["parcel_template_name"]):
			continue
		t = frappe.new_doc("Shipment Parcel Template")
		t.update(tmpl)
		t.skip_shipstation_sync = 1
		t.save()


# ──────────────────────────────────────────────────────────────────────────────
# Addresses and contacts
# ──────────────────────────────────────────────────────────────────────────────

# (city, state, zip, street, phone)
_CUSTOMER_ADDRESS_DATA = {
	customers[0]: ("Portland", "ME", "04101", "123 Ocean Avenue", "(207) 555-5678"),
	customers[1]: ("Boston", "MA", "02101", "456 Beacon Street", "(617) 555-1234"),
	customers[2]: ("Providence", "RI", "02903", "789 Westminster Street", "(401) 555-9876"),
}

# (first, last, email, phone, customer)
_CUSTOMER_CONTACT_DATA = {
	customers[0]: ("Shipping", "Contact", "shipping@almacs.example.com", "(207) 555-1234"),
	customers[1]: ("Bean", "Buyer", "orders@beansanddreams.example.com", "(617) 555-5678"),
	customers[2]: ("Cafe", "Manager", "freight@cafe27.example.com", "(401) 555-3456"),
}

_FREIGHT_TERMINAL = {
	"address_title": "Northeast Freight Terminal",
	"address_line1": "100 Terminal Drive",
	"city": "Worcester",
	"state": "MA",
	"pincode": "01601",
	"phone": "(508) 555-9999",
	"contact_first": "Terminal",
	"contact_last": "Agent",
	"contact_email": "agent@northeast-terminal.example.com",
	"contact_phone": "(508) 555-0001",
}


def create_customer_addresses_and_contacts(settings):
	"""Create ship-to addresses and shipping contacts for all test customers plus freight terminal."""
	for customer_name, (city, state, pincode, street, phone) in _CUSTOMER_ADDRESS_DATA.items():
		address_title = f"{customer_name} - {city}"
		if not frappe.db.exists("Address", {"address_title": address_title}):
			addr = frappe.new_doc("Address")
			addr.address_title = address_title
			addr.address_type = "Shipping"
			addr.address_line1 = street
			addr.city = city
			addr.state = state
			addr.pincode = pincode
			addr.country = "United States"
			addr.phone = phone
			addr.append("links", {"link_doctype": "Customer", "link_name": customer_name})
			addr.save()

	for customer_name, (first, last, email, phone) in _CUSTOMER_CONTACT_DATA.items():
		if not frappe.db.exists("Contact", {"first_name": first, "last_name": last}):
			contact = frappe.new_doc("Contact")
			contact.first_name = first
			contact.last_name = last
			contact.append("email_ids", {"email_id": email, "is_primary": 1})
			contact.append("phone_nos", {"phone": phone, "is_primary_phone": 1})
			contact.append("links", {"link_doctype": "Customer", "link_name": customer_name})
			contact.save()

	t = _FREIGHT_TERMINAL
	if not frappe.db.exists("Address", {"address_title": t["address_title"]}):
		addr = frappe.new_doc("Address")
		addr.address_title = t["address_title"]
		addr.address_type = "Shipping"
		addr.address_line1 = t["address_line1"]
		addr.city = t["city"]
		addr.state = t["state"]
		addr.pincode = t["pincode"]
		addr.country = "United States"
		addr.phone = t["phone"]
		addr.save()

	if not frappe.db.exists(
		"Contact", {"first_name": t["contact_first"], "last_name": t["contact_last"]}
	):
		contact = frappe.new_doc("Contact")
		contact.first_name = t["contact_first"]
		contact.last_name = t["contact_last"]
		contact.append("email_ids", {"email_id": t["contact_email"], "is_primary": 1})
		contact.append("phone_nos", {"phone": t["contact_phone"], "is_primary_phone": 1})
		contact.save()


# ──────────────────────────────────────────────────────────────────────────────
# Sales Orders
# ──────────────────────────────────────────────────────────────────────────────

# Each entry: (customer_index, [(item_code, qty), ...])
# Two SOs for customers[0] so the LTL shipment can span multiple delivery notes.
_SALES_ORDER_SPECS = [
	(0, [("Ambrosia Pie", 20), ("Gooseberry Pie", 10)]),  # LTL leg A
	(0, [("Double Plum Pie", 20), ("Kaduka Key Lime Pie", 10)]),  # LTL leg B
	(1, [("Ambrosia Pie", 5), ("Double Plum Pie", 5)]),  # small parcel
	(2, [("Gooseberry Pie", 10), ("Kaduka Key Lime Pie", 10)]),  # freight terminal
]


def create_sales_orders(settings):
	"""Create one Sales Order per entry in _SALES_ORDER_SPECS (idempotent by item set)."""
	for customer_idx, items in _SALES_ORDER_SPECS:
		customer = customers[customer_idx]
		item_codes = [ic for ic, _ in items]
		if _so_exists_with_items(customer, settings.company, item_codes):
			continue
		so = frappe.new_doc("Sales Order")
		so.transaction_date = settings.day
		so.customer = customer
		so.order_type = "Sales"
		so.currency = "USD"
		so.company = settings.company
		so.selling_price_list = "Bakery Wholesale"
		for item_code, qty in items:
			so.append(
				"items",
				{
					"item_code": item_code,
					"delivery_date": settings.day,
					"qty": qty,
					"warehouse": "Baked Goods - APC",
				},
			)
		so.save()
		so.submit()


def _so_exists_with_items(customer, company, item_codes):
	"""Return True if a Sales Order for this customer already has exactly these item_codes."""
	for so in frappe.get_all("Sales Order", filters={"customer": customer, "company": company}):
		so_items = frappe.get_all("Sales Order Item", filters={"parent": so.name}, pluck="item_code")
		if sorted(so_items) == sorted(item_codes):
			return True
	return False


# ──────────────────────────────────────────────────────────────────────────────
# Delivery Notes
# ──────────────────────────────────────────────────────────────────────────────


def create_delivery_notes(settings):
	"""Create one Delivery Note per Sales Order (idempotent)."""
	for customer_idx, items in _SALES_ORDER_SPECS:
		customer = customers[customer_idx]
		item_codes = [ic for ic, _ in items]
		so_name = _get_so_with_items(customer, settings.company, item_codes)
		if not so_name:
			continue
		if frappe.db.get_value(
			"Delivery Note Item", {"against_sales_order": so_name, "docstatus": 0}, "parent"
		):
			continue
		dn = make_delivery_note(so_name)
		dn.save()


def _get_so_with_items(customer, company, item_codes):
	"""Return the name of the first SO for this customer that matches these item_codes."""
	for so in frappe.get_all("Sales Order", filters={"customer": customer, "company": company}):
		so_items = frappe.get_all("Sales Order Item", filters={"parent": so.name}, pluck="item_code")
		if sorted(so_items) == sorted(item_codes):
			return so.name
	return None


# ──────────────────────────────────────────────────────────────────────────────
# Packing Slips (small parcel)
# ──────────────────────────────────────────────────────────────────────────────


def create_packing_slips(settings):
	"""Create Packing Slips for the small-parcel Delivery Note (customers[1])."""
	customer = customers[1]
	so_name = _get_so_with_items(customer, settings.company, ["Ambrosia Pie", "Double Plum Pie"])
	if not so_name:
		return
	dn_name = frappe.db.get_value(
		"Delivery Note Item",
		{"against_sales_order": so_name, "docstatus": 0},
		"parent",
	)
	if not dn_name:
		return
	dn = frappe.get_doc("Delivery Note", dn_name)
	if frappe.db.exists("Packing Slip", {"delivery_note": dn.name}):
		return

	customer_address = frappe.get_value(
		"Address",
		{"address_title": f"{customer} - Boston"},
		"name",
	)
	company_address = frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Company", "link_name": settings.company, "parenttype": "Address"},
		"parent",
	)

	ps = make_packing_slip(dn.name)
	ps.shipping_address_name = customer_address
	ps.dispatch_address_name = company_address
	ps.carrier = "USPS"
	ps.freight_type = "Small Parcel"
	ps.carrier_service = "usps_priority_mail"

	for idx, item in enumerate(ps.items):
		item.parcel_number = idx + 1
		item.parcel_length = 12
		item.parcel_width = 9
		item.parcel_height = 6
		item.dimension_uom = "Inch"
		item.parcel_weight = 3
		item.parcel_weight_uom = "Pound"

	ps.save()


# ──────────────────────────────────────────────────────────────────────────────
# Shipments
# ──────────────────────────────────────────────────────────────────────────────


def create_shipments(settings):
	create_shipment_for_ltl(settings)
	create_shipment_for_small_parcel(settings)
	create_shipment_for_freight_terminal(settings)


def _get_company_address(settings):
	return frappe.db.get_value(
		"Dynamic Link",
		{"link_doctype": "Company", "link_name": settings.company, "parenttype": "Address"},
		"parent",
	)


def _get_ltl_carrier():
	return frappe.get_value(
		"Supplier", {"supplier_name": "Test LTL Carrier", "is_transporter": 1}, "name"
	)


def create_shipment_for_ltl(settings):
	"""Draft LTL Shipment spanning both customers[0] Delivery Notes.

	Seeded with Shipment Delivery Note rows (parcel data included) so tests can
	call ``get_ltl_quotes`` or ``build_packages_from_sdn`` without extra setup.

	Billing: Shipper / Prepaid — triggers Purchase Invoice creation on SQ submit.
	"""
	if frappe.db.exists("Shipment", {"freight_type": "LTL", "docstatus": 0}):
		return

	company_address = _get_company_address(settings)
	ltl_carrier = _get_ltl_carrier()
	customer_address = frappe.get_value(
		"Address", {"address_title": f"{customers[0]} - Portland"}, "name"
	)
	shipping_contact = frappe.get_value(
		"Contact", {"first_name": "Shipping", "last_name": "Contact"}, "name"
	)

	# Both DNs for customers[0]
	dn_a_so = _get_so_with_items(customers[0], settings.company, ["Ambrosia Pie", "Gooseberry Pie"])
	dn_b_so = _get_so_with_items(
		customers[0], settings.company, ["Double Plum Pie", "Kaduka Key Lime Pie"]
	)
	dns = []
	for so_name in (dn_a_so, dn_b_so):
		if not so_name:
			continue
		dn_name = frappe.db.get_value(
			"Delivery Note Item", {"against_sales_order": so_name, "docstatus": 0}, "parent"
		)
		if dn_name:
			dns.append(frappe.get_doc("Delivery Note", dn_name))

	shipment = frappe.new_doc("Shipment")
	shipment.posting_date = settings.day
	shipment.pickup_date = settings.day
	shipment.pickup_from = "08:00:00"
	shipment.pickup_to = "17:00:00"
	shipment.pickup_from_type = "Company"
	shipment.pickup_company = settings.company
	shipment.pickup_address_name = company_address
	shipment.pickup_contact_person = "Administrator"
	shipment.delivery_to_type = "Customer"
	shipment.delivery_customer = customers[0]
	shipment.delivery_address_name = customer_address
	shipment.shipping_contact = shipping_contact
	shipment.preferred_carrier = ltl_carrier
	shipment.freight_type = "LTL"
	shipment.value_of_goods = 1500.00
	shipment.description_of_content = "Baked goods - assorted pies"
	shipment.billing_type = "Shipper"
	shipment.payment_terms = "Prepaid"
	shipment.billing_account = "TEST-ACCOUNT-001"
	# Two 48×40×36 in pallets at 250 lbs each
	shipment.total_length = 96
	shipment.total_width = 40
	shipment.total_height = 36
	shipment.total_weight = 500
	shipment.total_number_of_packages_or_handling_units = 2

	# Two pallets — one per DN — so shipment_parcel[0] has count=1 (clean density calculation)
	for _ in dns:
		shipment.append(
			"shipment_parcel",
			{
				"length": 48,
				"width": 40,
				"height": 36,
				"weight": 250,
				"count": 1,
				"length_uom": "Inch",
				"weight_uom": "Pound",
			},
		)

	# Add SDN rows with parcel data so tests work without further fixture setup
	parcel_number = 1
	for dn in dns:
		dn_items = frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn.name},
			fields=["name", "item_code", "item_name", "qty", "stock_uom"],
		)
		for item in dn_items:
			shipment.append(
				"shipment_delivery_note",
				{
					"delivery_note": dn.name,
					"dn_detail": item.name,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"qty": item.qty,
					"stock_uom": item.stock_uom,
					"parcel_number": parcel_number,
					"parcel_length": 48,
					"parcel_width": 40,
					"parcel_height": 36,
					"dimension_uom": "Inch",
					"parcel_weight": 125,
					"parcel_weight_uom": "Pound",
				},
			)
		parcel_number += 1

	shipment.save()


def create_shipment_for_small_parcel(settings):
	"""Draft small-parcel Shipment for customers[1].

	Billing: Shipper / Collect — no Purchase Invoice is auto-created on SQ submit.
	"""
	if frappe.db.exists("Shipment", {"freight_type": "Small Parcel", "docstatus": 0}):
		return

	company_address = _get_company_address(settings)
	customer_address = frappe.get_value(
		"Address", {"address_title": f"{customers[1]} - Boston"}, "name"
	)
	shipping_contact = frappe.get_value(
		"Contact", {"first_name": "Bean", "last_name": "Buyer"}, "name"
	)
	so_name = _get_so_with_items(customers[1], settings.company, ["Ambrosia Pie", "Double Plum Pie"])
	dn_name = frappe.db.get_value(
		"Delivery Note Item", {"against_sales_order": so_name, "docstatus": 0}, "parent"
	)
	if not dn_name:
		return
	dn = frappe.get_doc("Delivery Note", dn_name)

	shipment = frappe.new_doc("Shipment")
	shipment.posting_date = settings.day
	shipment.pickup_date = settings.day
	shipment.pickup_from = "08:00:00"
	shipment.pickup_to = "17:00:00"
	shipment.pickup_from_type = "Company"
	shipment.pickup_company = settings.company
	shipment.pickup_address_name = company_address
	shipment.pickup_contact_person = "Administrator"
	shipment.delivery_to_type = "Customer"
	shipment.delivery_customer = customers[1]
	shipment.delivery_address_name = customer_address
	shipment.shipping_contact = shipping_contact
	shipment.preferred_carrier = frappe.get_value(
		"Supplier", {"supplier_name": "USPS", "is_transporter": 1}, "name"
	)
	shipment.freight_type = "Small Parcel"
	shipment.value_of_goods = 150.00
	shipment.description_of_content = "Baked goods - assorted pies"
	shipment.billing_type = "Shipper"
	shipment.payment_terms = "Collect"

	shipment.append(
		"shipment_parcel",
		{
			"length": 12,
			"width": 9,
			"height": 6,
			"weight": 6,
			"count": 2,
			"length_uom": "Inch",
			"weight_uom": "Pound",
		},
	)

	if dn:
		dn_items = frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn.name},
			fields=["name", "item_code", "item_name", "qty", "stock_uom"],
		)
		for idx, item in enumerate(dn_items):
			shipment.append(
				"shipment_delivery_note",
				{
					"delivery_note": dn.name,
					"dn_detail": item.name,
					"item_code": item.item_code,
					"item_name": item.item_name,
					"qty": item.qty,
					"stock_uom": item.stock_uom,
					"parcel_number": idx + 1,
					"parcel_length": 12,
					"parcel_width": 9,
					"parcel_height": 6,
					"dimension_uom": "Inch",
					"parcel_weight": 3,
					"parcel_weight_uom": "Pound",
				},
			)

	shipment.save()


def create_shipment_for_freight_terminal(settings):
	"""Draft LTL Shipment for customers[2] destined for a freight terminal.

	Uses delivery_to_type="Contact" — the load goes to the Northeast Freight
	Terminal where the customer's transport collects it (carrier-terminal-pickup).

	Billing: Shipper / Prepaid.
	"""
	if frappe.db.exists(
		"Shipment",
		{"freight_type": "LTL", "delivery_to_type": "Contact", "docstatus": 0},
	):
		return

	company_address = _get_company_address(settings)
	ltl_carrier = _get_ltl_carrier()
	terminal_address = frappe.get_value(
		"Address", {"address_title": _FREIGHT_TERMINAL["address_title"]}, "name"
	)
	terminal_contact = frappe.get_value(
		"Contact",
		{
			"first_name": _FREIGHT_TERMINAL["contact_first"],
			"last_name": _FREIGHT_TERMINAL["contact_last"],
		},
		"name",
	)
	so_name = _get_so_with_items(
		customers[2], settings.company, ["Gooseberry Pie", "Kaduka Key Lime Pie"]
	)
	dn_name = frappe.db.get_value(
		"Delivery Note Item", {"against_sales_order": so_name, "docstatus": 0}, "parent"
	)
	if not dn_name:
		return
	dn = frappe.get_doc("Delivery Note", dn_name)

	shipment = frappe.new_doc("Shipment")
	shipment.posting_date = settings.day
	shipment.pickup_date = settings.day
	shipment.pickup_from = "08:00:00"
	shipment.pickup_to = "17:00:00"
	shipment.pickup_from_type = "Company"
	shipment.pickup_company = settings.company
	shipment.pickup_address_name = company_address
	shipment.pickup_contact_person = "Administrator"
	shipment.delivery_to_type = "Contact"
	shipment.delivery_address_name = terminal_address
	shipment.shipping_contact = terminal_contact
	shipment.preferred_carrier = ltl_carrier
	shipment.freight_type = "LTL"
	shipment.carrier_terminal_pickup = 1
	shipment.value_of_goods = 450.00
	shipment.description_of_content = "Baked goods - assorted pies (terminal hold)"
	shipment.billing_type = "Shipper"
	shipment.payment_terms = "Prepaid"
	shipment.billing_account = "TEST-ACCOUNT-001"
	shipment.total_length = 48
	shipment.total_width = 40
	shipment.total_height = 36
	shipment.total_weight = 250
	shipment.total_number_of_packages_or_handling_units = 1

	shipment.append(
		"shipment_parcel",
		{
			"length": 48,
			"width": 40,
			"height": 36,
			"weight": 250,
			"count": 1,
			"length_uom": "Inch",
			"weight_uom": "Pound",
		},
	)

	if dn:
		dn_items = frappe.get_all(
			"Delivery Note Item",
			filters={"parent": dn.name},
			fields=["name", "item_code", "item_name", "qty", "stock_uom"],
		)
		for item in dn_items:
			shipment.append(
				"shipment_delivery_note",
				{
					"delivery_note": dn.name,
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

	shipment.save()


# ──────────────────────────────────────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────────────────────────────────────


def load_ltl_json_fixture(filename: str) -> dict:
	fixtures_dir = Path(frappe.get_app_path("shipstation_integration", "tests", "fixtures"))
	return json.loads((fixtures_dir / filename).read_text())


def ltl_quotes_response_for_tests() -> dict:
	"""First captured ShipEngine LTL quotes response body (list of rate offers)."""
	return load_ltl_json_fixture("ltl_quotes_response.json")["captured_responses"][0]["response"]


def ltl_pickup_response_for_tests() -> dict:
	"""Captured schedule-pickup API response body."""
	return load_ltl_json_fixture("ltl_pickup_response.json")["captured_responses"][0]["response"]


def get_draft_ltl_shipment_for_tests():
	"""Draft LTL Shipment (customers[0], two DNs, prepaid) created by setup."""
	shipment = frappe.get_last_doc(
		"Shipment",
		filters={"freight_type": "LTL", "delivery_to_type": "Customer", "docstatus": 0},
	)
	shipment.reload()
	return shipment


def get_small_parcel_shipment_for_tests():
	"""Draft small-parcel Shipment (customers[1], collect billing) created by setup."""
	shipment = frappe.get_last_doc(
		"Shipment", filters={"freight_type": "Small Parcel", "docstatus": 0}
	)
	shipment.reload()
	return shipment


def get_freight_terminal_shipment_for_tests():
	"""Draft LTL Shipment to freight terminal (delivery_to_type=Contact) created by setup."""
	shipment = frappe.get_last_doc(
		"Shipment",
		filters={"freight_type": "LTL", "delivery_to_type": "Contact", "docstatus": 0},
	)
	shipment.reload()
	return shipment


_LTL_SHIPMENT_QUOTATION_RESET_FIELDS = (
	"accepted_quotation",
	"quote_or_offer_id",
	"quote_or_offer_transaction_id",
	"estimated_delivery_date",
	"shipment_amount",
	"pickup_id",
	"awb_number",
	"shipment_id",
	"payment_terms",
)


def reset_ltl_shipment_quotation_test_state() -> None:
	"""Clear quotation docs and LTL quote/pickup fields on the seed draft LTL Shipment.

	The Shipment is created once by ``create_shipment_for_ltl`` in ``create_test_data``. Tests
	should call this at the **start** of each case so state comes only from the database + setup,
	not from pytest fixtures or context managers.
	"""
	shipment = get_draft_ltl_shipment_for_tests()
	for sq in frappe.get_all("Shipment Quotation", filters={"shipment": shipment.name}, pluck="name"):
		sq_doc = frappe.get_doc("Shipment Quotation", sq)
		if sq_doc.docstatus == 1:
			sq_doc.flags.ignore_permissions = True
			sq_doc.cancel()
		frappe.delete_doc("Shipment Quotation", sq, force=True)
	# Create reset dict, excluding payment_terms which needs special handling
	reset_fields = [f for f in _LTL_SHIPMENT_QUOTATION_RESET_FIELDS if f != "payment_terms"]
	reset_values = {field: None for field in reset_fields}
	reset_values["shipment_amount"] = 0
	reset_values["payment_terms"] = "Prepaid"  # Reset to initial value, not None
	frappe.db.set_value("Shipment", shipment.name, reset_values)
