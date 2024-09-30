import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import frappe
from erpnext.stock.doctype.item.item import get_uom_conv_factor
from frappe.utils import flt, getdate
from frappe.utils.safe_exec import is_job_queued
from httpx import HTTPError

from shipstation_integration.customer import create_customer, get_billing_address
from shipstation_integration.items import create_item

if TYPE_CHECKING:
	from erpnext.selling.doctype.sales_order.sales_order import SalesOrder
	from shipstation.models import ShipStationOrder, ShipStationOrderItem

	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)
	from shipstation_integration.shipstation_integration.doctype.shipstation_store.shipstation_store import (
		ShipstationStore,
	)


def queue_orders():
	if not is_job_queued("shipstation_integration.orders.list_orders", queue="shipstation"):
		frappe.enqueue(
			method="shipstation_integration.orders.list_orders",
			queue="shipstation",
		)


def list_orders(
	settings: "ShipstationSettings" = None,
	last_order_datetime: datetime.datetime = None,
):
	if not settings:
		settings = frappe.get_all("Shipstation Settings", filters={"enabled": True})
	elif not isinstance(settings, list):
		settings = [settings]

	for sss in settings:
		sss_doc: "ShipstationSettings" = frappe.get_doc("Shipstation Settings", sss.name)
		if not sss_doc.enabled:
			continue

		client = sss_doc.client()
		client.timeout = 60 * 5

		if not last_order_datetime:
			# Get data for the last day, Shipstation API behaves oddly when it's a shorter period
			last_order_datetime = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

		store: "ShipstationStore"
		for store in sss_doc.shipstation_stores:
			if not store.enable_orders:
				continue

			parameters = {
				"store_id": store.store_id,
				"modify_date_start": last_order_datetime,
				"modify_date_end": datetime.datetime.utcnow(),
			}

			update_parameter_hook = frappe.get_hooks("update_shipstation_list_order_parameters")
			if update_parameter_hook:
				parameters = frappe.get_attr(update_parameter_hook[0])(parameters)

			try:
				orders = client.list_orders(parameters=parameters)
			except HTTPError as e:
				frappe.log_error(title="Error while fetching Shipstation orders", message=e)
				continue

			order: "ShipStationOrder"
			for order in orders:
				if validate_order(sss_doc, order, store):
					should_create_order = True

					process_order_hook = frappe.get_hooks("process_shipstation_order")
					if process_order_hook:
						should_create_order = frappe.get_attr(process_order_hook[0])(order, store)

					if should_create_order:
						create_erpnext_order(order, store, sss)


def validate_order(
	settings: "ShipstationSettings",
	order: "ShipStationOrder",
	store: "ShipstationStore",
):
	if not order:
		return False

	# if an order already exists, skip
	if frappe.db.get_value("Sales Order", {"shipstation_order_id": order.order_id, "docstatus": 1}):
		return False

	# only create orders for warehouses defined in Shipstation Settings;
	# if no warehouses are set, fetch everything
	if (
		settings.active_warehouse_ids
		and order.advanced_options.warehouse_id not in settings.active_warehouse_ids
	):
		return False

	# if a date filter is set in Shipstation Settings, don't create orders before that date
	if settings.since_date and getdate(order.create_date) < settings.since_date:
		return False

	return True


def create_erpnext_order(
	order: "ShipStationOrder", store: "ShipstationStore", settings: "ShipstationSettings"
) -> str | None:
	if settings.shipstation_user:
		frappe.set_user(settings.shipstation_user)
	customer = (
		frappe.get_cached_doc("Customer", store.customer) if store.customer else create_customer(order)
	)
	so: "SalesOrder" = frappe.new_doc("Sales Order")
	so.update(
		{
			"shipstation_store_name": store.store_name,
			"shipstation_order_id": order.order_id,
			"shipstation_customer_notes": getattr(order, "customer_notes", None),
			"shipstation_internal_notes": getattr(order, "internal_notes", None),
			"marketplace": store.marketplace_name,
			"marketplace_order_id": order.order_number,
			"customer": customer.name,
			"customer_name": order.customer_email,
			"company": store.company,
			"transaction_date": getdate(order.order_date),
			"delivery_date": getdate(order.ship_date),
			"shipping_address_name": customer.customer_primary_address,
			"customer_primary_address": get_billing_address(customer.name),
			"integration_doctype": "Shipstation Settings",
			"integration_doc": store.parent,
			"has_pii": True,
			"currency": store.currency,
		}
	)
	if store.sales_partner:
		so.sales_partner = store.sales_partner

	if store.get("is_amazon_store"):
		update_hook = frappe.get_hooks("update_shipstation_amazon_order")
		if update_hook:
			so = frappe.get_attr(update_hook[0])(store, order, so)
	elif store.get("is_shopify_store"):
		update_hook = frappe.get_hooks("update_shipstation_shopify_order")
		if update_hook:
			so = frappe.get_attr(update_hook[0])(store, order, so)

	# using `hasattr` over `getattr` to use type annotations
	order_items = order.items if hasattr(order, "items") else []
	if not order_items:
		return

	process_order_items_hook = frappe.get_hooks("process_shipstation_order_items")
	if process_order_items_hook:
		order_items = frappe.get_attr(process_order_items_hook[0])(order_items)

	discount_amount = 0.0
	for item in order_items:
		if item.quantity < 1:
			continue

		rate = flt(item.unit_price) if hasattr(item, "unit_price") else 0.0

		# the only way to identify marketplace discounts via the Shipstation API is
		# to find it using the `line_item_key` string
		if item.line_item_key == "discount":
			discount_amount += abs(rate * item.quantity)
			continue

		settings = frappe.get_doc("Shipstation Settings", store.parent)
		stock_item = create_item(item, settings=settings, store=store)
		uom = stock_item.sales_uom or stock_item.stock_uom
		conversion_factor = (
			1 if uom == stock_item.stock_uom else get_uom_conv_factor(uom, stock_item.stock_uom)
		)
		item_notes = get_item_notes(item)
		so.append(
			"items",
			{
				"item_code": stock_item.item_code,
				"qty": item.quantity,
				"uom": uom,
				"conversion_factor": conversion_factor,
				"rate": rate,
				"warehouse": store.warehouse,
				"shipstation_order_item_id": item.order_item_id,
				"shipstation_item_notes": item_notes,
			},
		)

	if not so.get("items"):
		return

	so.dont_update_if_missing = ["customer_name", "base_total_in_words"]

	if order.tax_amount:
		so.sales_tax_total = flt(order.tax_amount)
		so.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": store.tax_account,
				"description": "Shipstation Tax Amount",
				"tax_amount": order.tax_amount,
				"cost_center": store.cost_center,
			},
		)

	if order.shipping_amount:
		so.shipping_revenue = flt(order.shipping_amount)
		so.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": store.shipping_income_account,
				"description": "Shipstation Shipping Amount",
				"tax_amount": order.shipping_amount,
				"cost_center": store.cost_center,
			},
		)

	so.save()
	if store.customer:
		so.customer_name = order.customer_email
	# coupons
	if order.amount_paid and Decimal(so.grand_total).quantize(Decimal(".01")) != order.amount_paid:
		difference_amount = Decimal(Decimal(so.grand_total).quantize(Decimal(".01")) - order.amount_paid)
		so.shipstation_discount = difference_amount
		account = store.difference_account
		# if the shipping amount is noted but not charged (FBA orders), this correctly offsets it
		if difference_amount == order.shipping_amount:
			account = store.shipping_income_account
		so.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": account,
				"description": "Shipstation Difference Amount",
				"tax_amount": -1 * difference_amount,
				"cost_center": store.cost_center,
			},
		)

	if order.tax_amount and store.withholding:
		# reverse withholding
		so.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": store.tax_account,
				"description": "Shipstation Tax Amount",
				"tax_amount": order.tax_amount * -1,
				"cost_center": store.cost_center,
			},
		)

	if discount_amount > 0:
		so.apply_discount_on = "Grand Total"
		so.discount_amount = discount_amount

	if so.sales_partner and store.apply_commission:
		so.calculate_commission()
		if so.total_commission:
			so.append(
				"taxes",
				{
					"charge_type": "Actual",
					"account_head": store.commission_account,
					"cost_center": store.cost_center,
					"description": f"Commission of {so.get_formatted('total_commission')}",
					"tax_amount": -(so.total_commission),
					"included_in_paid_amount": 1,
				},
			)

	so.save()

	before_submit_hook = frappe.get_hooks("update_shipstation_order_before_submit")
	if before_submit_hook:
		so = frappe.get_attr(before_submit_hook[0])(store, so, order)
		if so:
			so.save()
	if so:
		so.submit()
		frappe.db.commit()

	after_submit_hook = frappe.get_hooks("update_shipstation_order_after_submit")
	if before_submit_hook:
		frappe.get_attr(after_submit_hook[0])(store, so, order)
		frappe.db.commit()

	if order.tag_ids:
		for tag_id in order.tag_ids:
			tag_name = frappe.db.exists("Tag", {"custom_tag_id": tag_id})
			if tag_name:
				so.add_tag(tag_name)
				frappe.db.commit()

	return so.name if so else None


def get_item_notes(item: "ShipStationOrderItem"):
	notes = None
	item_options = item.options if hasattr(item, "options") else None
	if item_options:
		for option in item_options:
			if option.name == "Description":
				notes = option.value
				break
	return notes
