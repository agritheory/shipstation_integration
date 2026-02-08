import frappe
import httpx
from frappe import _
from frappe.utils import now

from shipstation_integration.carriers import _get_settings


@frappe.whitelist()
def sync_parcel_template(template_name: str):
	doc = frappe.get_doc("Shipment Parcel Template", template_name)

	if not doc.length or not doc.width or not doc.height:
		frappe.throw(_("Package dimensions are required"))

	if not doc.package_code:
		frappe.throw(_("Package Code is required"))

	settings = _get_settings(None)
	api_key = settings.get_password("shipstation_api_key")

	package_code = doc.package_code
	if not package_code.startswith("custom_"):
		package_code = f"custom_{package_code}"

	payload = {
		"package_code": package_code,
		"name": doc.parcel_template_name,
		"dimensions": {
			"unit": "centimeter",
			"length": doc.length,
			"width": doc.width,
			"height": doc.height,
		},
	}

	try:
		with httpx.Client(timeout=30) as client:
			response = client.post(
				"https://api.shipstation.com/v2/packages",
				headers={
					"API-Key": api_key,
					"Content-Type": "application/json",
				},
				json=payload,
			)

		if response.status_code not in (200, 201):
			frappe.throw(
				_("ShipStation error ({0}): {1}").format(
					response.status_code,
					response.text,
				)
			)

		data = response.json()

		doc.db_set(
			{
				"package_id": data.get("package_id"),
				"package_code": data.get("package_code"),
				"is_package_synced": 1,
			}
		)

		frappe.msgprint(_("Package synced successfully with ShipStation"))

	except Exception as e:
		frappe.log_error(
			title="ShipStation Package Sync Failed",
			message=frappe.get_traceback(),
		)
		raise
