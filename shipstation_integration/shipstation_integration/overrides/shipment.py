import frappe
from erpnext.stock.doctype.shipment.shipment import Shipment


class ShipStationShipment(Shipment):
	def on_submit(self):
		super().on_submit()
		seventeentrack = frappe.get_single("Seventeen Track")
		if (
			self.amended_from
			and frappe.db.get_value(self.doctype, self.amended_from, "shipment_id") == self.shipment_id
		):
			seventeentrack.retrack(self.shipment_id, self.seventeen_track_carrier)
		else:
			seventeentrack.track_shipment_id(self.shipment_id, self.seventeen_track_carrier)

	def on_cancel(self):
		super().on_cancel()
		seventeentrack = frappe.get_single("Seventeen Track")
		seventeentrack.stop_tracking(self.shipment_id, self.seventeen_track_carrier)
