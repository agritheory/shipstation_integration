# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	validate_filters(filters)
	return get_columns(), get_data(filters)


def validate_filters(filters):
	if not filters.get("from_date"):
		filters.from_date = add_days(today(), -180)
	if not filters.get("to_date"):
		filters.to_date = today()
	if getdate(filters.from_date) > getdate(filters.to_date):
		frappe.throw(_("From Date cannot be after To Date"))


def get_columns():
	return [
		{"label": _("Type"), "fieldname": "shipment_type", "fieldtype": "Data", "width": 100},
		{"label": _("Ship Date"), "fieldname": "ship_date", "fieldtype": "Date", "width": 100},
		{
			"label": _("Document Type"),
			"fieldname": "document_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 120,
		},
		{
			"label": _("Document"),
			"fieldname": "document",
			"fieldtype": "Dynamic Link",
			"options": "document_type",
			"width": 160,
		},
		{
			"label": _("Delivery Note"),
			"fieldname": "delivery_note",
			"fieldtype": "Link",
			"options": "Delivery Note",
			"width": 150,
		},
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 160,
		},
		{"label": _("Sales Channel"), "fieldname": "sales_channel", "fieldtype": "Data", "width": 120},
		{
			"label": _("Sales Order"),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 140,
		},
		{"label": _("Customer PO"), "fieldname": "customer_po", "fieldtype": "Data", "width": 120},
		{"label": _("Carrier"), "fieldname": "carrier", "fieldtype": "Data", "width": 120},
		{"label": _("Service"), "fieldname": "service", "fieldtype": "Data", "width": 140},
		{"label": _("Containers"), "fieldname": "containers", "fieldtype": "Int", "width": 90},
		{"label": _("Tracking / PRO"), "fieldname": "tracking", "fieldtype": "Data", "width": 180},
		{"label": _("Freight Term"), "fieldname": "freight_term", "fieldtype": "Data", "width": 120},
		{"label": _("Freight Cost"), "fieldname": "freight_cost", "fieldtype": "Currency", "width": 120},
	]


def get_data(filters):
	rows = []
	if include_packing_slips(filters):
		rows.extend(get_packing_slip_rows(filters))
	if include_shipments(filters):
		rows.extend(get_shipment_rows(filters))
	rows.sort(
		key=lambda row: (row.get("ship_date") or getdate("1900-01-01"), row.get("document") or ""),
		reverse=True,
	)
	return rows


def include_packing_slips(filters):
	shipment_type = filters.get("shipment_type")
	return not shipment_type or shipment_type in ("All", "Parcel")


def include_shipments(filters):
	shipment_type = filters.get("shipment_type")
	return not shipment_type or shipment_type == "All" or shipment_type != "Parcel"


def matches_type_filter(filters, shipment_type):
	selected = filters.get("shipment_type")
	return not selected or selected == "All" or selected == shipment_type


def get_packing_slip_rows(filters):
	tracked = documents_with_tracking("Packing Slip Item")
	if not tracked:
		return []

	slip_filters = {"name": ["in", tracked], "docstatus": ["in", [0, 1]]}
	if filters.get("carrier"):
		slip_filters["carrier"] = filters.carrier

	slips = frappe.get_all(
		"Packing Slip",
		filters=slip_filters,
		fields=["name", "delivery_note", "carrier", "carrier_service", "creation"],
	)
	if not slips:
		return []

	delivery_notes = delivery_note_map([row.delivery_note for row in slips if row.delivery_note])
	item_stats = child_line_stats(
		"Packing Slip Item",
		[row.name for row in slips],
		sales_order_field="against_sales_order",
	)
	notes_without_so = [
		row.delivery_note
		for row in slips
		if row.delivery_note and not item_stats.get(row.name, {}).get("sales_orders")
	]
	fallback_orders = sales_orders_for_delivery_notes(notes_without_so)
	freight_costs = delivery_note_freight_costs(list(delivery_notes))

	rows = []
	for slip in slips:
		dn = delivery_notes.get(slip.delivery_note) or frappe._dict()
		if not row_matches_party_filters(filters, company=dn.company, customer=dn.customer):
			continue

		ship_date = dn.posting_date or getdate(slip.creation)
		if not in_date_range(ship_date, filters):
			continue

		stats = item_stats.get(slip.name) or empty_line_stats()
		sales_order = first_or_none(stats["sales_orders"]) or first_or_none(
			fallback_orders.get(slip.delivery_note) or []
		)
		rows.append(
			frappe._dict(
				{
					"shipment_type": "Parcel",
					"ship_date": ship_date,
					"document_type": "Packing Slip",
					"document": slip.name,
					"delivery_note": slip.delivery_note,
					"customer": dn.customer,
					"sales_channel": sales_channel_from_doc(dn),
					"sales_order": sales_order,
					"customer_po": dn.po_no,
					"carrier": slip.carrier,
					"service": slip.carrier_service,
					"containers": len(stats["parcels"]),
					"tracking": join_unique(stats["tracking"]),
					"freight_term": None,
					"freight_cost": flt(freight_costs.get(slip.delivery_note)),
				}
			)
		)
	return rows


def get_shipment_rows(filters):
	shipment_filters = {"docstatus": ["in", [0, 1]], "awb_number": ["is", "set"]}
	if filters.get("customer"):
		shipment_filters["delivery_customer"] = filters.customer

	fields = [
		"name",
		"pickup_date",
		"delivery_customer",
		"carrier",
		"carrier_service",
		"awb_number",
		"payment_terms",
		"shipment_amount",
		"pickup_company",
		"freight_type",
	]
	fields.extend(
		present_fields(
			"Shipment",
			["preferred_carrier", "carrier_service_level", "marketplace", "shipstation_store_name"],
		)
	)

	shipments = frappe.get_all("Shipment", filters=shipment_filters, fields=fields)
	if filters.get("carrier"):
		carrier = filters.carrier
		shipments = [
			row for row in shipments if row.carrier == carrier or row.get("preferred_carrier") == carrier
		]
	if not shipments:
		return []

	names = [row.name for row in shipments]
	sdn_stats = child_line_stats(
		"Shipment Delivery Note",
		names,
		sales_order_field="against_sales_order",
		delivery_note_field="delivery_note",
	)
	delivery_note_names = []
	for stats in sdn_stats.values():
		delivery_note_names.extend(stats.get("delivery_notes") or [])
	delivery_notes = delivery_note_map(delivery_note_names)
	fallback_orders = sales_orders_for_delivery_notes(
		[
			dn_name
			for stats in sdn_stats.values()
			for dn_name in (stats.get("delivery_notes") or [])
			if not stats.get("sales_orders")
		]
	)

	rows = []
	for shipment in shipments:
		stats = sdn_stats.get(shipment.name) or empty_line_stats()
		delivery_note = first_or_none(stats.get("delivery_notes") or [])
		dn = delivery_notes.get(delivery_note) or frappe._dict()
		company = shipment.pickup_company or dn.company
		customer = shipment.delivery_customer or dn.customer
		if not row_matches_party_filters(filters, company=company, customer=customer):
			continue

		ship_date = shipment.pickup_date
		if not in_date_range(ship_date, filters):
			continue

		shipment_type = shipment.freight_type or "LTL"
		if not matches_type_filter(filters, shipment_type):
			continue

		sales_order = first_or_none(stats["sales_orders"]) or first_or_none(
			fallback_orders.get(delivery_note) or []
		)
		rows.append(
			frappe._dict(
				{
					"shipment_type": shipment_type,
					"ship_date": ship_date,
					"document_type": "Shipment",
					"document": shipment.name,
					"delivery_note": delivery_note,
					"customer": customer,
					"sales_channel": sales_channel_from_doc(shipment) or sales_channel_from_doc(dn),
					"sales_order": sales_order,
					"customer_po": dn.po_no,
					"carrier": shipment.carrier or shipment.get("preferred_carrier"),
					"service": shipment.carrier_service or shipment.get("carrier_service_level"),
					"containers": len(stats["parcels"]),
					"tracking": shipment.awb_number,
					"freight_term": shipment.payment_terms,
					"freight_cost": flt(shipment.shipment_amount),
				}
			)
		)
	return rows


def documents_with_tracking(child_doctype):
	rows = frappe.get_all(
		child_doctype,
		filters={"tracking_number": ["is", "set"]},
		fields=["parent", "tracking_number"],
	)
	return list({row.parent for row in rows if row.tracking_number})


def delivery_note_map(names):
	names = list({name for name in names if name})
	if not names:
		return {}
	fields = ["name", "posting_date", "customer", "company", "po_no"]
	fields.extend(present_fields("Delivery Note", ["marketplace", "shipstation_store_name"]))
	return {
		row.name: row
		for row in frappe.get_all("Delivery Note", filters={"name": ["in", names]}, fields=fields)
	}


def child_line_stats(child_doctype, parents, sales_order_field=None, delivery_note_field=None):
	if not parents:
		return {}
	fields = ["parent", "parcel_number", "tracking_number"]
	fields.extend(present_fields(child_doctype, [sales_order_field, delivery_note_field]))
	stats = {}
	for row in frappe.get_all(child_doctype, filters={"parent": ["in", parents]}, fields=fields):
		entry = stats.setdefault(row.parent, empty_line_stats())
		if row.parcel_number:
			entry["parcels"].add(row.parcel_number)
		if row.tracking_number:
			append_unique(entry["tracking"], row.tracking_number)
		if sales_order_field and row.get(sales_order_field):
			append_unique(entry["sales_orders"], row.get(sales_order_field))
		if delivery_note_field and row.get(delivery_note_field):
			append_unique(entry["delivery_notes"], row.get(delivery_note_field))
	return stats


def empty_line_stats():
	return {"parcels": set(), "tracking": [], "sales_orders": [], "delivery_notes": []}


def sales_orders_for_delivery_notes(names):
	names = list({name for name in names if name})
	if not names:
		return {}
	orders = {}
	for row in frappe.get_all(
		"Delivery Note Item",
		filters={"parent": ["in", names], "against_sales_order": ["is", "set"]},
		fields=["parent", "against_sales_order"],
	):
		append_unique(orders.setdefault(row.parent, []), row.against_sales_order)
	return orders


def delivery_note_freight_costs(names):
	names = list({name for name in names if name})
	if not names:
		return {}
	costs = {}
	for row in frappe.get_all(
		"Sales Taxes and Charges",
		filters={"parenttype": "Delivery Note", "parent": ["in", names], "charge_type": "Actual"},
		fields=["parent", "description", "tax_amount"],
	):
		if row.description and "ship" in row.description.lower():
			costs[row.parent] = costs.get(row.parent, 0) + flt(row.tax_amount)
	return costs


def sales_channel_from_doc(doc):
	return doc.get("marketplace") or doc.get("shipstation_store_name")


def row_matches_party_filters(filters, company=None, customer=None):
	if filters.get("company") and company != filters.company:
		return False
	if filters.get("customer") and customer != filters.customer:
		return False
	return True


def in_date_range(ship_date, filters):
	if not ship_date:
		return True
	return getdate(filters.from_date) <= getdate(ship_date) <= getdate(filters.to_date)


def present_fields(doctype, fieldnames):
	meta = frappe.get_meta(doctype)
	return [name for name in fieldnames if name and meta.has_field(name)]


def append_unique(values, value):
	if value and value not in values:
		values.append(value)


def join_unique(values):
	return ", ".join(values) if values else None


def first_or_none(values):
	return values[0] if values else None
