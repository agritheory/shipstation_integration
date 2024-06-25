import frappe
from erpnext.selling.doctype.sales_order.sales_order import SalesOrder


class ShipStationSalesOrder(SalesOrder):
	def calculate_commission(self):
		commission_formula = frappe.get_cached_value(
			"Sales Partner", self.sales_partner, "commission_formula"
		)
		if not self.shipstation_order_id or not commission_formula:
			super().calculate_commission()
		elif self.shipstation_order_id and commission_formula:
			self.total_commission = get_formula_based_commission(self, commission_formula)


def get_formula_based_commission(doc, commission_formula=None):
	if not commission_formula:
		commission_formula = frappe.get_cached_value(
			"Sales Partner", doc.sales_partner, "commission_formula"
		)

	eval_globals = frappe._dict(
		{
			"frappe": frappe._dict({"get_value": frappe.db.get_value, "get_all": frappe.db.get_all}),
			"flt": frappe.utils.data.flt,
			"min": min,
			"max": max,
		}
	)
	eval_locals = {
		"doc": doc,
	}

	try:
		return frappe.safe_eval(commission_formula, eval_globals=eval_globals, eval_locals=eval_locals)
	except Exception as e:
		print("Error evaluating commission formula:\n", e)
		frappe.log_error(title="Error evaluating commission formula", message=e)
		return None


# flt((( doc.grand_total * 0.1325) * 0.9)  + 0.40, 2)
# flt(((
# 	(min(doc.total, 2500) * 0.1235) + (max(doc.total - 2500, 0) *.0235)
# ) * 0.9)  + 0.40, 2)
