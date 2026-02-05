import frappe


def execute():
	create_field("User", "length_uom", "Length UOM", "Link", "UOM")

	create_field("User", "weight_uom", "Weight UOM", "Link", "UOM")


def create_field(dt, fieldname, label, fieldtype, options):
	if frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fieldname}):
		return

	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": dt,
			"fieldname": fieldname,
			"label": label,
			"fieldtype": fieldtype,
			"options": options,
			"insert_after": "language",
		}
	).insert()
