import frappe
from erpnext.setup.utils import set_defaults_for_tests
from frappe.desk.page.setup_wizard.setup_wizard import setup_complete
from frappe.utils import getdate


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
	frappe.db.commit()


def create_test_data():
	create_customer_group()
	create_price_list()


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
