import frappe

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
		ShipstationSettings,
	)


@frappe.whitelist()
def list_tags():
	settings = frappe.get_all("Shipstation Settings", filters={"enabled": True})
	for sss in settings:
		sss_doc: "ShipstationSettings" = frappe.get_doc("Shipstation Settings", sss.name)
		client = sss_doc.client()
		tags = client.list_tags()
		for tag in tags:
			if frappe.db.exists("Tag", tag.get("name")):
				continue
			tag_doc = frappe.new_doc("Tag")
			tag_doc.update(
				{
					"name": tag.get("name"),
					"custom_color": tag.get("color"),
					"custom_tag_id": tag.get("tagId"),
				}
			)
			tag_doc.save()