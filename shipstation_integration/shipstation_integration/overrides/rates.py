def get_parcels_from_shipment(shipment):
	parcels = []

	for p in shipment.shipment_parcel:
		parcels.append(
			{
				"length": p.length,
				"width": p.width,
				"height": p.height,
				"weight": p.weight,
				"packageCode": p.package_code,
			}
		)

	return parcels
