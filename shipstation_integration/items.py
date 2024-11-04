# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

from typing import TYPE_CHECKING, Optional

import frappe
from frappe.utils import flt
from shipstation.models import ShipStationItem, ShipStationOrderItem

if TYPE_CHECKING:
	from erpnext.stock.doctype.item.item import Item

	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)
	from shipstation_integration.shipstation_integration.doctype.shipstation_store.shipstation_store import (
		ShipstationStore,
	)


def create_item(
	product: ShipStationItem | ShipStationOrderItem,
	settings: "ShipstationSettings",
	store: Optional["ShipstationStore"] = None,
) -> str:

	if settings.shipstation_user:
		frappe.set_user(settings.shipstation_user)

	item_name = product.name[:140]
	if not product.sku:
		item_code = frappe.db.get_value("Item", {"item_name": item_name.strip()})
	else:
		item_code = frappe.db.get_value("Item", {"item_code": product.sku.strip()})
		item_name = frappe.db.get_value("Item", item_code, "item_name") or item_name

	if item_code:
		item: "Item" = frappe.get_doc("Item", item_code)
	else:
		weight_per_unit, weight_uom = 1.0, "Ounce"
		if isinstance(product, ShipStationItem):
			weight_per_unit = flt(getattr(product, "weight_oz", 1))
		elif isinstance(product, ShipStationOrderItem):
			weight = product.weight if hasattr(product, "weight") else frappe._dict()
			if weight:
				weight_per_unit = flt(weight.value) if weight else 1
				weight_uom = weight.units.title() if weight and weight.units else "Ounce"

				if weight_uom.lower() == "ounces":
					# map Shipstation UOM to ERPNext UOM
					weight_uom = "Ounce"
				elif weight_uom.lower() == "grams":
					# map Shipstation UOM to ERPNext UOM
					weight_uom = "Gram"
				elif weight_uom.lower() in ("pound", "pounds", "lb", "lbs"):
					# map Shipstation UOM to ERPNext UOM
					weight_uom = "Pound"

				if settings.weight_conversion == "Convert to Gram" and weight_uom == "Ounce":
					weight_per_unit = flt(weight_per_unit * 28.3495, 2)
					weight_uom = "Gram"
				elif settings.weight_conversion == "Convert to Gram" and weight_uom == "Pound":
					weight_per_unit = flt(weight_per_unit * 453.592, 2)
					weight_uom = "Gram"
				elif settings.weight_conversion == "Convert to Ounce" and weight_uom == "Gram":
					weight_per_unit = flt(weight_per_unit * 0.035274, 2)
					weight_uom = "Ounce"

		item: "Item" = frappe.new_doc("Item")
		item.update(
			{
				"item_code": product.sku or item_name,
				"item_name": item_name,
				"item_group": settings.default_item_group,
				"is_stock_item": True,
				"include_item_in_manufacturing": False,
				"description": getattr(product, "internal_notes", product.name),
				"weight_per_unit": weight_per_unit,
				"weight_uom": weight_uom,
				"end_of_life": "",
			}
		)

	if item.disabled:
		# override disabled status to be able to add the item to the order
		item.disabled = False
		item.add_comment(
			comment_type="Edit",
			text="re-enabled this item after a new order was fetched from Shipstation",
		)

	# create item defaults, if missing
	if store:
		item.update(
			{
				"integration_doctype": "Shipstation Settings",
				"integration_doc": store.parent,
				"store": store.name,
			}
		)

		if store.company and not item.get("item_defaults"):
			item.set(
				"item_defaults",
				[
					{
						"company": store.company,
						"default_price_list": "ShipStation",
						"default_warehouse": "",  # leave unset
						"buying_cost_center": store.cost_center,
						"selling_cost_center": store.cost_center,
						"expense_account": store.expense_account,
						"income_account": store.sales_account,
					}
				],
			)

	before_save_hook = frappe.get_hooks("update_shipstation_item_before_save")
	if before_save_hook:
		item = frappe.get_attr(before_save_hook[0])(store, item)

	try:
		item.save()
		frappe.db.commit()
	except frappe.TimestampMismatchError:
		frappe.log_error(
			title=f"Timestamp Mismatch Error for Item {item_name}", message=frappe.get_traceback()
		)
		item.reload()
		item.save()
		frappe.db.commit()
	except Exception as e:
		print("Error saving Item:\n", e)
		frappe.log_error(title=f"Error saving Item {item_name}", message=frappe.get_traceback())

	return item
