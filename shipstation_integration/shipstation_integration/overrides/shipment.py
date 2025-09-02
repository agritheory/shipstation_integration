import frappe
from erpnext.stock.doctype.shipment.shipment import Shipment


class ShipStationShipment(Shipment):
	def on_submit(self):
		super().on_submit()
		seventeentrack = frappe.get_single("Seventeen Track")
		if self.amended_from:
			seventeentrack.retrack(self.shipment_id, self.carrier)
		else:
			seventeentrack.track_shipment_id(self.shipment_id, self.carrier)

	def on_cancel(self):
		super().on_cancel()
		seventeentrack = frappe.get_single("Seventeen Track")
		seventeentrack.stop_tracking(self.shipment_id, self.carrier)
