import json
from urllib.parse import parse_qs, urlparse

import frappe
from frappe import _
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


@frappe.whitelist(allow_guest=True)
def shipstation_api_webhook():
	"""
	Handle ShipStation API v2 webhooks.

	Supported events:
	- batch: Batch label processing completion
	- track: Tracking updates
	- carrier_connected: New carrier connected
	- label_created: Label creation notification
	- sales_order_status_change: Order status changes
	"""
	try:
		data = frappe.local.form_dict or json.loads(frappe.local.request.data)
	except Exception:
		frappe.log_error(
			title="ShipStation API Webhook Error",
			message="Failed to parse webhook payload",
		)
		return {"status": "error", "message": "Invalid payload"}

	event_type = data.get("event") or data.get("resource_type")

	if not event_type:
		return {"status": "ignored", "message": "No event type specified"}

	# Log the webhook for debugging
	frappe.logger("shipstation").debug(f"Webhook received: {event_type}\n{json.dumps(data, indent=2)}")

	# Route to appropriate handler
	handlers = {
		"batch": _handle_batch_complete,
		"track": _handle_tracking_update,
		"carrier_connected": _handle_carrier_connected,
		"label_created": _handle_label_created,
		"sales_order_status_change": _handle_order_status_change,
	}

	handler = handlers.get(event_type)
	if handler:
		try:
			return handler(data)
		except Exception as e:
			frappe.log_error(
				title=f"ShipStation API Webhook Handler Error: {event_type}",
				message=str(e),
			)
			return {"status": "error", "message": str(e)}

	return {"status": "ignored", "message": f"Unknown event type: {event_type}"}


def _handle_batch_complete(data: dict) -> dict:
	"""Handle batch label processing completion."""
	batch_id = data.get("data", {}).get("batch_id") or data.get("batch_id")

	if not batch_id:
		return {"status": "ignored", "message": "No batch_id in payload"}

	# Log batch completion
	frappe.logger("shipstation").debug(f"Batch {batch_id} processing complete")

	# Could trigger follow-up actions like downloading labels
	# For now, just acknowledge
	return {"status": "success", "message": f"Batch {batch_id} acknowledged"}


def _handle_tracking_update(data: dict) -> dict:
	"""Handle tracking status updates."""
	tracking_data = data.get("data", {})
	tracking_number = tracking_data.get("tracking_number")
	status = tracking_data.get("status_description") or tracking_data.get("status")

	if not tracking_number:
		return {"status": "ignored", "message": "No tracking_number in payload"}

	# Find and update Delivery Note with this tracking number
	delivery_notes = frappe.get_all(
		"Delivery Note",
		filters={"tracking_number": tracking_number, "docstatus": 1},
		pluck="name",
	)

	for dn_name in delivery_notes:
		frappe.db.set_value(
			"Delivery Note",
			dn_name,
			"tracking_status",
			status,
		)

	# Also update Shipment documents
	shipments = frappe.get_all(
		"Shipment",
		filters={"awb_number": tracking_number, "docstatus": 1},
		pluck="name",
	)

	for shipment_name in shipments:
		frappe.db.set_value(
			"Shipment",
			shipment_name,
			"tracking_status",
			status,
		)

	return {
		"status": "success",
		"message": f"Updated {len(delivery_notes)} delivery notes and {len(shipments)} shipments",
	}


def _handle_carrier_connected(data: dict) -> dict:
	"""Handle new carrier connection notification."""
	carrier_data = data.get("data", {})
	carrier_name = carrier_data.get("friendly_name") or carrier_data.get("carrier_code")

	frappe.logger("shipstation").info(f"New carrier connected: {carrier_name}")

	return {"status": "success", "message": f"Carrier {carrier_name} connection noted"}


def _handle_label_created(data: dict) -> dict:
	"""Handle label creation notification."""
	label_data = data.get("data", {})
	label_id = label_data.get("label_id")
	tracking_number = label_data.get("tracking_number")

	if tracking_number:
		# Try to update any matching Delivery Notes
		delivery_notes = frappe.get_all(
			"Delivery Note",
			filters={
				"docstatus": 1,
				"tracking_number": ["is", "not set"],
				"shipstation_order_id": label_data.get("order_id"),
			},
			pluck="name",
			limit=1,
		)

		for dn_name in delivery_notes:
			frappe.db.set_value(
				"Delivery Note",
				dn_name,
				{
					"tracking_number": tracking_number,
					"carrier": label_data.get("carrier_code", "").upper(),
				},
			)

	return {"status": "success", "message": f"Label {label_id} noted"}


def _handle_order_status_change(data: dict) -> dict:
	"""Handle order status change notification."""
	order_data = data.get("data", {})
	order_id = order_data.get("order_id")
	new_status = order_data.get("order_status")

	if order_id:
		# Update Sales Order if exists
		sales_orders = frappe.get_all(
			"Sales Order",
			filters={"shipstation_order_id": order_id, "docstatus": 1},
			pluck="name",
		)

		for so_name in sales_orders:
			frappe.db.set_value(
				"Sales Order",
				so_name,
				"shipstation_order_status",
				new_status,
			)

	return {"status": "success", "message": f"Order {order_id} status update noted"}
