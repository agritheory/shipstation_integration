import json
from urllib.parse import parse_qs, urlparse

import frappe


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
	sss_doc = frappe.get_doc("Shipstation Settings", store.shipstation_settings)
	client = sss_doc.client()

	if resource_type == "ORDER_NOTIFY":
		response = client.get(
			endpoint="/orders", payload={"storeID": store_id, "importBatch": import_batch}
		)
		response.raise_for_status()
		for order in response.json().get("orders", []):
			frappe.enqueue(
				method="shipstation_integration.orders.create_order_from_webhook",
				queue="shipstation",
				order=order,
				store=store.name,
				settings=sss_doc.name,
			)

	elif resource_type == "SHIP_NOTIFY":
		response = client.get(
			endpoint="/shipments", payload={"storeID": store_id, "importBatch": import_batch}
		)
		response.raise_for_status()
		for shipment in response.json().get("shipments", []):
			frappe.enqueue(
				method="shipstation_integration.shipments.create_shipment_from_webhook",
				queue="shipstation",
				shipment=shipment,
				store=store.name,
				settings=sss_doc.name,
			)

	return
