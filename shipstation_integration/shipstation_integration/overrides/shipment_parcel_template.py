import frappe
import requests


class ShipStationClient:
	def __init__(self):
		self.api_key = frappe.conf.shipstation_api_key
		self.api_secret = frappe.conf.shipstation_api_secret
		self.base_url = "https://ssapi.shipstation.com"

	def _headers(self):
		return {"Content-Type": "application/json"}

	def get(self, endpoint):
		r = requests.get(
			f"{self.base_url}{endpoint}",
			auth=(self.api_key, self.api_secret),
			headers=self._headers(),
		)
		r.raise_for_status()
		return r.json()

	def list_carriers(self):
		return self.get("/carriers")


@frappe.whitelist()
def sync_shipstation_packages():
	client = ShipStationClient()
	carriers = client.list_carriers()

	for carrier in carriers:
		carrier_code = carrier.get("carrierCode")
		carrier_name = carrier.get("name")

		for pkg in carrier.get("packages", []):
			package_code = pkg.get("packageCode")
			package_name = pkg.get("name")

			# Unique per carrier + package
			exists = frappe.db.exists(
				"Shipment Parcel Template",
				{
					"carrier": carrier_code,
					"package_code": package_code,
				},
			)

			if exists:
				continue

			doc = frappe.new_doc("Shipment Parcel Template")
			doc.parcel_template_name = f"{carrier_name} - {package_name}"
			doc.carrier = carrier_code
			doc.package_code = package_code
			doc.package_name = package_name
			doc.is_shipstation_package = 1

			# ShipStation packages often don't have fixed dimensions
			doc.length = 1
			doc.width = 1
			doc.height = 1
			doc.weight = 0.1

			doc.insert(ignore_permissions=True)
