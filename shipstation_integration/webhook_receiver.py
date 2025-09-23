# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
from urllib.parse import parse_qs, urlparse

import frappe
from frappe.utils import getdate
from shipstation.models import ShipStationOrder

from shipstation_integration.orders import validate_order


@frappe.whitelist(allow_guest=True)
def shipstation_webhook():
	data = frappe.local.form_dict or json.loads(frappe.local.request.data)
	resource_type = data.get("resource_type")
	resource_url = data.get("resource_url")

	if not resource_url:
		return

	parsed_qs = parse_qs(urlparse(data["resource_url"]).query)
	store_id = parsed_qs.get("storeID", [None])[0]
	import_batch = parsed_qs.get("importBatch", [None])[0]
	store = frappe.get_doc("Shipstation Store", {"store_id": store_id})
	sss_doc = frappe.get_doc("Shipstation Settings", store.parent)

	if not sss_doc.enabled:
		return

	client = sss_doc.client()

	if resource_type == "ORDER_NOTIFY":
		if not store.enable_orders:
			return

		response = client.get(
			endpoint="/orders", payload={"storeID": store_id, "importBatch": import_batch}
		)
		response.raise_for_status()
		for order in response.json().get("orders", []):
			ss_order = ShipStationOrder().json(order)

			if not validate_order(sss_doc, ss_order, store):
				continue

			should_create_order = True

			process_order_hook = frappe.get_hooks("process_shipstation_order")
			if process_order_hook:
				should_create_order = frappe.get_attr(process_order_hook[0])(ss_order, store)

			if not should_create_order:
				continue

			frappe.enqueue(
				method="shipstation_integration.orders.create_order_from_webhook",
				queue="shipstation",
				order=order,
				store=store.name,
				settings=sss_doc.name,
			)

	elif resource_type in ["SHIP_NOTIFY", "ITEM_SHIP_NOTIFY"]:
		if not store.enable_shipments or not any(
			[
				store.create_sales_invoice,
				store.create_delivery_note,
				store.create_shipment,
			]
		):
			return

		response = client.get(
			endpoint="/shipments", payload={"storeID": store_id, "importBatch": import_batch}
		)
		response.raise_for_status()
		for shipment in response.json().get("shipments", []):
			ss_shipment = ShipStationOrder().json(shipment)

			if sss_doc.since_date and getdate(shipment.create_date) < sss_doc.since_date:
				continue

			if (
				frappe.db.exists(
					"Delivery Note",
					{"docstatus": 1, "shipstation_order_id": ss_shipment.order_id},
				)
				and not shipment.voided
			):
				continue

			frappe.enqueue(
				method="shipstation_integration.shipments.create_shipment_from_webhook",
				queue="shipstation",
				shipment=shipment,
				store=store.name,
				settings=sss_doc.name,
			)

	return
