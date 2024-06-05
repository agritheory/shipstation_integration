import frappe
from erpnext.selling.doctype.sales_order.sales_order import SalesOrder


class ShipStationSalesOrder(SalesOrder):
	def calculate_commission(self):
		if not frappe.db.get_value("Sales Partner", self.sales_partner, "commission_formula"):
			super().calculate_commission()
