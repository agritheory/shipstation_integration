# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.nestedset import get_root_of
from httpx import HTTPError
from shipengine import ShipEngine
from shipstation import ShipStation
from shipstation.models import ShipStationWebhook

from shipstation_integration.items import create_item
from shipstation_integration.orders import list_orders
from shipstation_integration.shipments import list_shipments
from shipstation_integration.tags import list_tags
from shipstation_integration.utils import get_marketplace


class ShipstationSettings(Document):
	@property
	def store_ids(self):
		stores = json.loads(self.store_data)
		stores = [json.loads(s) for s in stores]
		return [s.get("storeId") for s in stores]

	@property
	def active_warehouse_ids(self) -> list[str]:
		warehouse_ids = []

		for warehouse in self.shipstation_warehouses:
			warehouse_id = frappe.db.get_value(
				"Warehouse", warehouse.get("warehouse"), "shipstation_warehouse_id"
			)
			warehouse_ids.append(warehouse_id)

		return warehouse_ids

	def onload(self):
		if self.carrier_data:
			self.set_onload("carriers", self._carrier_data())

	def validate(self):
		self.validate_label_generation()
		self.validate_enabled_stores()

	def before_insert(self):
		self.validate_api_connection()

	def after_insert(self):
		if self.enabled:
			self.update_carriers_and_stores()
			self.update_warehouses()
			self.add_webhooks()

	def on_update(self):
		if self.enabled:
			self.add_webhooks()

	@frappe.whitelist()
	def get_orders(self):
		list_orders(self)

	@frappe.whitelist()
	def get_shipments(self):
		list_shipments(self)

	@frappe.whitelist()
	def get_tags(self):
		list_tags(self)

	def client(self):
		return ShipStation(
			key=self.get_password("api_key"),
			secret=self.get_password("api_secret"),
			debug=False,
			timeout=30,
		)

	def shipstation_api_client(self):
		"""Returns a ShipEngine client for ShipStation API v2."""
		if not self.enable_shipstation_api:
			frappe.throw(_("ShipStation API v2 is not enabled"))
		api_key = self.get_password("shipstation_api_key")
		if not api_key:
			frappe.throw(_("ShipStation API key not configured"))
		return ShipEngine(api_key=api_key)

	@frappe.whitelist()
	def test_shipstation_api_connection(self):
		"""Test the ShipStation API v2 connection."""
		try:
			client = self.shipstation_api_client()
			# Test by fetching carriers
			result = client.list_carriers()
			frappe.msgprint(_("Connection successful! Found {0} carriers.").format(len(result)))
			return True
		except Exception as e:
			frappe.throw(_("Connection failed: {0}").format(str(e)))

	@frappe.whitelist()
	def fetch_api_carriers(self):
		"""Fetch carrier data from ShipStation API v2."""
		try:
			client = self.shipstation_api_client()
			carriers = client.list_carriers()

			carrier_list = []
			for carrier in carriers:
				carrier_data = {
					"carrier_id": carrier.get("carrier_id"),
					"carrier_code": carrier.get("carrier_code"),
					"account_number": carrier.get("account_number"),
					"name": carrier.get("friendly_name") or carrier.get("nickname"),
					"services": [],
					"packages": [],
				}

				# Fetch services for this carrier
				try:
					services = client.list_carrier_services(carrier.get("carrier_id"))
					carrier_data["services"] = [
						{
							"service_code": s.get("service_code"),
							"name": s.get("name"),
							"domestic": s.get("domestic"),
							"international": s.get("international"),
						}
						for s in services
					]
				except Exception as e:
					frappe.logger("shipstation").warning(f"Failed to fetch carrier services for {carrier.get('carrier_code')}: {e}")

				# Fetch packages for this carrier
				try:
					packages = client.list_carrier_package_types(carrier.get("carrier_id"))
					carrier_data["packages"] = [
						{
							"package_code": p.get("package_code"),
							"name": p.get("name"),
						}
						for p in packages
					]
				except Exception as e:
					frappe.logger("shipstation").warning(f"Failed to fetch carrier packages for {carrier.get('carrier_code')}: {e}")

				carrier_list.append(carrier_data)

			self.shipstation_api_carrier_data = json.dumps(carrier_list)
			self.save()
			frappe.msgprint(_("Successfully fetched {0} carriers from ShipStation API v2.").format(len(carrier_list)))
			return carrier_list
		except Exception as e:
			frappe.throw(_("Failed to fetch carriers: {0}").format(str(e)))

	def _api_carrier_data(self):
		"""Return parsed API carrier data."""
		if not self.shipstation_api_carrier_data:
			return []
		return json.loads(self.shipstation_api_carrier_data)

	def get_api_carrier_codes(self, carrier_name, service_name, package_name=None):
		"""Get carrier, service, and package codes from API carrier data."""
		_carrier_id, _service_code, _package_code = None, None, None

		for carrier in self._api_carrier_data():
			if carrier_name in [carrier.get("name"), carrier.get("carrier_code")]:
				_carrier_id = carrier.get("carrier_id")

				for service in carrier.get("services", []):
					if service.get("name") == service_name:
						_service_code = service.get("service_code")
						break

				if package_name:
					for package in carrier.get("packages", []):
						if package.get("name") == package_name:
							_package_code = package.get("package_code")
							break

				break

		return _carrier_id, _service_code, _package_code

	def validate_label_generation(self):
		if not self.enabled and self.enable_label_generation:
			self.enable_label_generation = False

	def validate_enabled_stores(self):
		for store in self.shipstation_stores:
			if store.enable_shipments and not store.enable_orders:
				store.enable_shipments = False
				store.create_sales_invoice = False
				store.create_delivery_note = False
				store.create_shipment = False

	def validate_api_connection(self):
		if not self.enabled:
			return
		try:
			client = self.client()
			client.list_carriers()
		except HTTPError as e:
			if e.response.status_code == 401:
				frappe.throw(_("Invalid API key or secret"))
			else:
				frappe.throw(_(e.text))

	@frappe.whitelist()
	def update_carriers_and_stores(self):
		client = self.client()

		unstructured_carriers = []
		carriers = client.list_carriers()
		for carrier in carriers:
			carrier_dict = carrier._unstructure()
			services = client.list_services(carrier.code)
			carrier_dict["services"] = [s._unstructure() for s in services]
			packages = client.list_packages(carrier.code)
			carrier_dict["packages"] = [p._unstructure() for p in packages]
			unstructured_carriers.append(carrier_dict)

		self.carrier_data = json.dumps(unstructured_carriers)
		self.update_stores()
		self.save()
		return self

	@frappe.whitelist()
	def update_warehouses(self):
		self.shipstation_warehouses = []
		root_warehouse = get_root_of("Warehouse")

		if not frappe.db.exists("Warehouse", {"warehouse_name": "Shipstation Warehouses"}):
			ss_warehouse_doc = frappe.new_doc("Warehouse")
			ss_warehouse_doc.update(
				{
					"warehouse_name": "Shipstation Warehouses",
					"parent_warehouse": root_warehouse,
					"is_group": True,
				}
			)
			ss_warehouse_doc.insert()

		parent_warehouse = frappe.get_doc("Warehouse", {"warehouse_name": "Shipstation Warehouses"})
		warehouses = self.client().list_warehouses()

		for warehouse in warehouses:
			if frappe.db.exists("Warehouse", {"shipstation_warehouse_id": warehouse.warehouse_id}):
				warehouse_doc = frappe.get_doc(
					"Warehouse", {"shipstation_warehouse_id": warehouse.warehouse_id}
				)
			else:
				warehouse_doc = frappe.new_doc("Warehouse")
				warehouse_doc.update(
					{
						"shipstation_warehouse_id": warehouse.warehouse_id,
						"warehouse_name": warehouse.warehouse_name,
						"parent_warehouse": parent_warehouse.name,
					}
				)
				warehouse_doc.insert()

			self.append("shipstation_warehouses", {"warehouse": warehouse_doc.name})

		self.save()

	def update_stores(self):
		stores = self.client().list_stores(show_inactive=False)
		for store in stores:
			store_exists = False
			for ss_store in self.shipstation_stores:
				if store.store_id == ss_store.store_id:
					ss_store.update(
						{
							"marketplace_name": store.marketplace_name,
							"store_name": store.store_name,
						}
					)
					store_exists = True

			if store_exists:
				continue

			if "Amazon" in store.marketplace_name:
				self.append(
					"shipstation_stores",
					{
						"is_amazon_store": 1,
						"amazon_marketplace": store.account_name,
						"enable_orders": 1,
						"store_id": store.store_id,
						"marketplace_name": get_marketplace(id=store.account_name).sales_partner,
						"store_name": store.store_name,
					},
				)
			elif "Shopify" in store.marketplace_name:
				self.append(
					"shipstation_stores",
					{
						"is_shopify_store": 1,
						"enable_orders": 1,
						"store_id": store.store_id,
						"marketplace_name": store.marketplace_name,
						"store_name": store.store_name,
					},
				)
			else:
				self.append(
					"shipstation_stores",
					{
						"enable_orders": 1,
						"store_id": store.store_id,
						"marketplace_name": store.marketplace_name,
						"store_name": store.store_name,
					},
				)

		return self

	@frappe.whitelist()
	def get_items(self):
		products = self.client().list_products()

		if not products.results:
			return "No products found to import"

		for product in products:
			create_item(product, settings=self)

		return f"{len(products.results)} product(s) imported succesfully"

	def _carrier_data(self):
		return json.loads(self.carrier_data)

	def get_carrier_services(self, carrier):
		for ss_carrier in self._carrier_data():
			if carrier in [ss_carrier["name"], ss_carrier["nickname"]]:
				return "\n".join([s["name"] for s in ss_carrier["services"]])

	def get_codes(self, carrier, service, package):
		_carrier, _service, _package = None, None, "Package"
		for ss_carrier in self._carrier_data():
			if carrier in [ss_carrier.get("name"), ss_carrier.get("nickname")]:
				_carrier = ss_carrier["code"]

				for serv in ss_carrier["services"]:
					if serv["name"] == service:
						_service = serv["code"]

				for pack in ss_carrier["packages"]:
					if pack["name"] == package:
						_package = pack["code"]

		return _carrier, _service, _package

	def add_webhooks(self):
		if not self.enabled:
			return

		WEBHOOK_RECEIVER_URL = f"{frappe.utils.get_url()}/api/method/shipstation_integration.webhook_receiver.shipstation_webhook"
		WEBHOOK_TYPES = [
			"ORDER_NOTIFY",
			"SHIP_NOTIFY",
			"ITEM_SHIP_NOTIFY",
		]

		client = self.client()
		existing_webhooks = client.list_webhooks()

		for store in self.shipstation_stores:
			for webhook_type in WEBHOOK_TYPES:
				filtered_webhook = list(
					filter(
						lambda webhook: webhook.store_id == store.store_id
						and webhook.hook_type == webhook_type
						and webhook.url == WEBHOOK_RECEIVER_URL,
						existing_webhooks,
					)
				)
				if not filtered_webhook:
					webhook = ShipStationWebhook(
						active=True,
						store_id=store.store_id,
						resource_type=webhook_type,
						event=webhook_type,
						hook_type=webhook_type,
						url=WEBHOOK_RECEIVER_URL,
						target_url=WEBHOOK_RECEIVER_URL,
						friendly_name="ERPNext",
						name="ERPNext",
					)
					client.subscribe_to_webhook(webhook)

		# Also add v2 webhooks if enabled
		self.add_v2_webhooks()

	def add_v2_webhooks(self):
		"""Register webhooks for ShipStation API v2."""
		if not self.enable_shipstation_api:
			return

		try:
			api_key = self.get_password("shipstation_api_key")
			if not api_key:
				return

			import httpx

			WEBHOOK_URL = f"{frappe.utils.get_url()}/api/method/shipstation_integration.webhook_receiver.shipstation_api_webhook"
			V2_WEBHOOK_EVENTS = ["batch", "track"]

			headers = {
				"API-Key": api_key,
				"Content-Type": "application/json",
			}

			# List existing webhooks
			with httpx.Client() as client:
				response = client.get(
					"https://api.shipstation.com/v2/environment/webhooks",
					headers=headers,
				)
				if response.status_code != 200:
					return

				existing_webhooks = response.json().get("webhooks", [])
				existing_urls = {w.get("url") for w in existing_webhooks}

				# Only create if not already registered
				if WEBHOOK_URL not in existing_urls:
					for event in V2_WEBHOOK_EVENTS:
						client.post(
							"https://api.shipstation.com/v2/environment/webhooks",
							headers=headers,
							json={
								"url": WEBHOOK_URL,
								"event": event,
							},
						)
		except Exception as e:
			frappe.log_error(
				title="Failed to register ShipStation API v2 webhooks",
				message=str(e),
			)
