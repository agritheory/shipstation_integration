from typing import TYPE_CHECKING

import frappe

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
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
					"custom_color": tag.color,
					"custom_tag_id": tag.tag_id,
				}
			)
			tag_doc.save()
			frappe.db.commit()
