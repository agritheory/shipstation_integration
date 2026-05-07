# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Shipment ⇄ Delivery Note item linkage.

The ERPNext ``Shipment`` child table ``Shipment Delivery Note`` was originally DN-level only.
Shipstation adds item-level linkage via ``dn_detail`` (link to ``Delivery Note Item``). Selecting
only **Delivery Note** in the grid does not populate **DN Item**; desk users can miss it until
something downstream expects ``dn_detail`` (rates, HU back-links, LTL payloads).

Hooks on **Shipment** (see ``hooks.py`` ``doc_events``) auto-fill ``dn_detail`` when the DN line
match is unique; ambiguous rows fail validation until the user picks DN Item explicitly.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.utils import flt


def delivery_note_item_match_for_shipment_line(
	delivery_note: str | None,
	item_code: str | None = None,
	row_qty: float | None = None,
) -> dict[str, Any] | None:
	"""Pick the ``Delivery Note Item`` row name + fields when the match is unique.

	Rules:

	1. ``Delivery Note`` has exactly one item line → use it.
	2. Several lines → ``item_code`` matches exactly one line → use it.
	3. Same ``item_code`` on multiple lines → ``row_qty`` equal to DN line qty for exactly one
	   of those lines → use it.

	Returns a dict shaped like ``frappe.get_all`` output, or ``None`` if ambiguous.
	"""
	if not delivery_note:
		return None

	items = frappe.get_all(
		"Delivery Note Item",
		filters={"parent": delivery_note},
		fields=[
			"name",
			"item_code",
			"item_name",
			"qty",
			"stock_uom",
			"amount",
			"base_amount",
		],
		order_by="idx asc",
		limit_page_length=500,
	)
	if len(items) == 1:
		return dict(items[0])
	if not items:
		return None

	code = (item_code or "").strip()
	q_match = row_qty if row_qty is None else flt(row_qty)

	if code:
		by_code = [i for i in items if i.item_code == code]
		if len(by_code) == 1:
			return dict(by_code[0])
		if len(by_code) > 1 and q_match is not None:
			by_qty = [i for i in by_code if flt(i.qty) == q_match]
			if len(by_qty) == 1:
				return dict(by_qty[0])

	return None


def apply_delivery_note_item_to_shipment_dn_row(row: Any, dn_item: dict[str, Any]) -> None:
	"""Copy matched Delivery Note Item fields onto a ``Shipment Delivery Note`` grid row."""
	row.dn_detail = dn_item["name"]
	row.item_code = dn_item["item_code"]
	row.item_name = dn_item.get("item_name")
	row.qty = flt(dn_item.get("qty"))
	row.stock_uom = dn_item.get("stock_uom")
	row.grand_total = flt(dn_item.get("base_amount") or dn_item.get("amount"))


def before_validate_shipment(doc, method=None) -> None:
	"""Ensure each Shipment ⇄ DN row has ``dn_detail`` populated when deterministically matchable."""

	if doc.doctype != "Shipment":
		return

	missing_rows: list[int] = []
	for row in doc.get("shipment_delivery_note") or []:
		if not getattr(row, "delivery_note", None):
			continue
		if getattr(row, "dn_detail", None):
			continue

		if not frappe.db.exists("Delivery Note", row.delivery_note):
			missing_rows.append(int(row.idx or 1))
			continue

		cnt = frappe.db.count("Delivery Note Item", filters={"parent": row.delivery_note})
		if cnt == 0:
			missing_rows.append(int(row.idx or 1))
			continue

		match = delivery_note_item_match_for_shipment_line(
			row.delivery_note,
			getattr(row, "item_code", None),
			getattr(row, "qty", None),
		)
		if match:
			apply_delivery_note_item_to_shipment_dn_row(row, match)
		else:
			missing_rows.append(int(row.idx or 1))

	if missing_rows:
		idx_list = ", ".join(str(i) for i in sorted(set(missing_rows)))
		frappe.throw(
			_(
				"Shipment Delivery Note row(s) {0} link to a Delivery Note but have no "
				"<b>DN Item</b> set. Either pick **DN Item** in the grid, or set **Item** and "
				"**Qty** so the line matches a unique Delivery Note line."
			).format(idx_list),
			title=_("DN Item required"),
		)
