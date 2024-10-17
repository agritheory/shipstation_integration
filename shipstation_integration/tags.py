# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

from typing import TYPE_CHECKING

import frappe
from frappe.utils.safe_exec import is_job_queued

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


def queue_tags():
	if not is_job_queued("shipstation_integration.orders.list_tags", queue="shipstation"):
		frappe.enqueue(
			method="shipstation_integration.orders.list_tags",
			queue="shipstation",
		)


@frappe.whitelist()
def list_tags(
	settings: "ShipstationSettings" = None,
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
		tags = client.list_tags()

		for tag in tags:
			if frappe.db.exists("Tag", tag.name):
				continue
			tag_doc = frappe.new_doc("Tag")
			tag_doc.update(
				{
					"name": tag.name,
					"color": tag.color,
					"tag_id": tag.tag_id,
				}
			)
			tag_doc.save()
			frappe.db.commit()
