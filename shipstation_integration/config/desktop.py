# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

from frappe import _


def get_data():
	return [
		{
			"module_name": "Shipstation Integration",
			"color": "grey",
			"icon": "octicon octicon-file-directory",
			"type": "module",
			"label": _("Shipstation Integration"),
		}
	]
