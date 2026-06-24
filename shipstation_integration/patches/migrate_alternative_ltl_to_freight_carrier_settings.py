# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe.utils.password import get_decrypted_password


def execute():
	"""Copy legacy Shipstation Settings alternative LTL passwords to Freight Carrier Settings when unambiguous."""
	if not frappe.db.table_exists("tabFreight Carrier Settings"):
		return

	for name in frappe.get_all("Shipstation Settings", pluck="name"):
		cid = get_decrypted_password(
			"Shipstation Settings", name, "alternative_ltl_client_id", raise_exception=False
		)
		csec = get_decrypted_password(
			"Shipstation Settings", name, "alternative_ltl_client_secret", raise_exception=False
		)
		if not cid and not csec:
			continue

		company = frappe.defaults.get_global_default("company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)
		if not company:
			company = frappe.db.get_value("Company", {}, "name", order_by="creation asc")
		if not company:
			frappe.log_error(
				title="Alternative LTL migration skipped",
				message=f"Shipstation Settings {name}: no default company found.",
			)
			continue

		transporters = frappe.get_all("Supplier", filters={"is_transporter": 1}, pluck="name")
		if len(transporters) != 1:
			frappe.log_error(
				title="Alternative LTL migration skipped",
				message=(
					f"Shipstation Settings {name}: need exactly one Supplier with is_transporter=1 "
					f"to auto-map legacy alternative LTL credentials (found {len(transporters)})."
				),
			)
			continue
		supplier = transporters[0]

		if frappe.db.exists(
			"Freight Carrier Settings", {"company": company, "supplier": supplier, "disabled": 0}
		):
			frappe.log_error(
				title="Alternative LTL migration skipped",
				message=f"Freight Carrier Settings already exists for company={company} supplier={supplier}.",
			)
			continue

		doc = frappe.new_doc("Freight Carrier Settings")
		doc.company = company
		doc.supplier = supplier
		doc.insert(ignore_permissions=True)
		if cid:
			doc.set("client_id", cid)
		if csec:
			doc.set("client_secret", csec)
		if cid or csec:
			doc.save(ignore_permissions=True)
