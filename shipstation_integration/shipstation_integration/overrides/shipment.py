import frappe
from erpnext.stock.doctype.shipment.shipment import Shipment


class ShipStationShipment(Shipment):
	def on_submit(self):
		super().on_submit()
		if self.amended_from:
			self.retrack()
		else:
			self.track_shipment_id()

	def on_cancel(self):
		super().on_cancel()
		self.stop_tracking()

	def retrack(self):
		seventeentrack = frappe.get_single("Seventeen Track")
		seventeentrack.retrack(self.shipment_id, self.carrier)

	def track_shipment_id(self):
		seventeentrack = frappe.get_single("Seventeen Track")
		seventeentrack.track_shipment_id(self.shipment_id, self.carrier)

	def stop_tracking(self):
		seventeentrack = frappe.get_single("Seventeen Track")
		seventeentrack.stop_tracking(self.shipment_id, self.carrier)
