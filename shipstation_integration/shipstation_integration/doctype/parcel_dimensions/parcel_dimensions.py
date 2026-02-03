# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ParcelDimensions(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		bol_url: DF.Data | None
		dimension_uom: DF.Link
		height: DF.Float
		label_url: DF.Data | None
		length: DF.Float
		parcel_template: DF.Link | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		tracking_number: DF.Data | None
		tracking_url: DF.Data | None
		weight: DF.Float
		weight_uom: DF.Link
		width: DF.Float
	# end: auto-generated types

	pass
