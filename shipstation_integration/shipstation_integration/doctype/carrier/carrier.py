# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe import _
from frappe.model.document import Document

CACHE_KEY = "seventeentrack_carriers"


class Carrier(Document):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self._table_fieldnames = []

	@staticmethod
	def get_current_data() -> dict[str, dict]:
		data = frappe.cache().get_value(CACHE_KEY)
		if data:
			return data
		carriers = requests.get("https://res.17track.net/asset/carrier/info/apicarrier.all.json").json()
		data = {}
		for carrier in carriers:
			data[str(carrier.get("key"))] = frappe._dict(
				{
					"name": str(carrier.get("key")),
					"key": str(carrier.get("key")),
					"carrier_name": carrier.get("_name"),
					"country": carrier.get("_country_iso"),
					"website": carrier.get("_url"),
					"phone": carrier.get("_tel", ""),
					"email": carrier.get("_email", ""),
				}
			)
		frappe.cache().set_value(CACHE_KEY, data)
		return data

	@staticmethod
	def get_list(args):
		filters = args.get("filters", [])
		or_filters = args.get("or_filters", [])
		limit_page_length = args.get("limit_page_length", 20)
		limit_start = args.get("limit_start", 0)

		carriers = Carrier.get_current_data()
		all_items = []

		def check_condition(row, cond):
			if isinstance(cond, list) and len(cond) >= 4:
				field = cond[1]
				op = cond[2]
				value = cond[3]
			elif isinstance(cond, dict):
				field = cond.get("field")
				op = cond.get("op", "=")
				value = cond.get("value")
			else:
				return True

			field_value = row.get("carrier_name")
			if op == "=":
				return field_value.lower() == value.lower()
			elif op == "like":
				return value.replace("%", "").lower() in field_value.lower()
			elif op == "in":
				return field_value.lower() in value.lower()
			elif op == "!=":
				return field_value.lower() != value.lower()
			else:
				return True

		for name, doc in carriers.items():
			match = True
			for cond in filters:
				if not check_condition(doc, cond):
					match = False
					break

			if or_filters:
				match = any(check_condition(doc, cond) for cond in or_filters)

			if match:
				all_items.append(doc)

		paginated = all_items[limit_start : limit_start + limit_page_length]

		if args.get("as_list"):
			paginated = tuple((row["name"], row["carrier_name"]) for row in paginated)
		else:
			paginated = [row for row in paginated]
		return paginated

	@staticmethod
	def get_count(args):
		carriers = Carrier.get_current_data()
		return len(carriers)

	@staticmethod
	def get_stats(args):
		return {}

	def load_from_db(self):
		data = self.get_current_data()
		d = data.get(self.name)
		if not d:
			return
		super(Document, self).__init__(d)

	def db_insert(self, *args, **kwargs):
		pass

	def db_update(self):
		pass

	def delete(self):
		pass


@frappe.whitelist()
def fetch_carriers():
	frappe.cache().delete_value(CACHE_KEY)
	Carrier.get_current_data()
	return _("Carriers Updated.")
