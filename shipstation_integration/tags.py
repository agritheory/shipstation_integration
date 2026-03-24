# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

from typing import TYPE_CHECKING, Any

import frappe
from frappe.utils.safe_exec import is_job_queued

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


def queue_tags():
	if not is_job_queued("shipstation_integration.tags.list_tags", queue="shipstation"):
		frappe.enqueue(
			method="shipstation_integration.tags.list_tags",
			queue="shipstation",
		)


@frappe.whitelist()
def list_tags(settings: Any = None):
	if not settings:
		settings = frappe.get_all("Shipstation Settings", filters={"enabled": True})
	elif not isinstance(settings, list):
		settings = [settings]

	for row in settings:
		settings_doc: "ShipstationSettings" = frappe.get_doc("Shipstation Settings", row.name)
		if not settings_doc.enabled or not settings_doc.enable_legacy_api:
			continue

		client = settings_doc.client()
		tags = client.list_tags()
		if settings_doc.shipstation_user:
			frappe.set_user(settings_doc.shipstation_user)
		for tag in tags:
			if frappe.db.exists("Tag", tag.name):
				tag_doc = frappe.get_doc("Tag", tag.name)
			else:
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
