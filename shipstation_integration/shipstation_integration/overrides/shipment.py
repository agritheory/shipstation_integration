import frappe
from erpnext.stock.doctype.shipment.shipment import Shipment


class ShipStationShipment(Shipment):
	def on_submit(self):
		super().on_submit()
		self.track_shipment_id()

	def track_shipment_id(self):
		seventeentrack = frappe.get_single("Seventeen Track")
		seventeentrack.track_shipment_id(self.shipment_id, self.carrier)
