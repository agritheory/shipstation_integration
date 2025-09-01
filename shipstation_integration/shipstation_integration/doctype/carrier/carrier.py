# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import frappe
import requests
from frappe.model.document import Document


class Carrier(Document):
	@staticmethod
	def get_current_data() -> dict[str, dict]:
		cache_key = "carrier_json_cache"
		data = frappe.cache().get_value(cache_key)
		if not data:
			carriers = requests.get("https://res.17track.net/asset/carrier/info/apicarrier.all.json").json()
			data = {}
			for carrier in carriers:
				data[str(carrier.get("key"))] = frappe._dict(
					{
						"name": str(carrier.get("key")),
						"carrier_name": carrier.get("_name"),
						"country": carrier.get("_country_iso"),
						"website": carrier.get("_url"),
						"phone": carrier.get("_tel", ""),
						"email": carrier.get("_email", ""),
					}
				)
			frappe.cache().set_value(cache_key, data)  # expires_in_sec=3600
		return data

	@staticmethod
	def get_list(args):
		carriers = Carrier.get_current_data()
		all_items = [frappe._dict(doc) for name, doc in carriers.items()]
		start = int(args.get("start") or 0)
		page_length = int(args.get("page_length") or 20)
		paginated = all_items[start : start + page_length]
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
		super(Document, self).__init__(d)

	def db_insert(self, *args, **kwargs):
		pass

	def db_update(self):
		pass

	def delete(self):
		pass
