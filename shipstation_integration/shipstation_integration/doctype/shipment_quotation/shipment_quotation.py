# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import add_days, today

from shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
	get_freight_carrier_settings,
)
from shipstation_integration.utils import get_shipment_company_for_ltl


class ShipmentQuotation(Document):
	def autoname(self):
		prefix = f"SQ-{self.shipment}-{self.carrier_scac}-"
		self.name = prefix + getseries(prefix, 2)

	def validate(self):
		self.calculate_estimated_delivery_date()

	def on_submit(self):
		self.validate_single_accepted_quote()
		self.set_or_reset_shipment_quote_fields()
		self.create_freight_accounting_entry()

	def on_cancel(self):
		self.set_or_reset_shipment_quote_fields(reset_fields=True)
		self.cancel_freight_accounting_entry()

	def calculate_estimated_delivery_date(self):
		if self.pickup_date and self.estimated_delivery_days and not self.estimated_delivery_date:
			self.estimated_delivery_date = add_days(self.pickup_date, self.estimated_delivery_days)

	def validate_single_accepted_quote(self):
		other = frappe.get_all(
			"Shipment Quotation",
			filters={"shipment": self.shipment, "docstatus": 1, "name": ["!=", self.name]},
		)
		if other:
			frappe.throw(
				msg=_(
					"{0} is already the accepted quotation for Shipment {1}. Cancel it before accepting a different quote."
				).format(other[0].name, self.shipment),
				title=_("Shipment Already Has an Accepted Quotation"),
			)

	def set_or_reset_shipment_quote_fields(self, reset_fields: bool = False) -> None:
		dt, dn = "Shipment", self.shipment
		quote_fields = ["quote_or_offer_id", "quote_or_offer_transaction_id", "estimated_delivery_date"]
		for field in quote_fields:
			frappe.db.set_value(dt, dn, field, None if reset_fields else self.get(field))

		frappe.db.set_value(dt, dn, "accepted_quotation", None if reset_fields else self.name)
		frappe.db.set_value(dt, dn, "shipment_amount", 0 if reset_fields else self.grand_total)

	def create_freight_accounting_entry(self) -> None:
		"""Create the appropriate accounting document based on shipment billing configuration.

		No-ops when Freight Carrier Settings has auto_create_accounting_entry disabled —
		the customer will perform manual reconciliation instead.

		Logical tree (when enabled)
		---------------------------
		Consignee billing
		  → nothing: customer pays the carrier directly; no entry needed here.

		Shipper billing, Prepaid, delivery_customer is set  (chargeback scenario)
		  → Journal Entry
		       DR  freight_expense_account    (cost recognised immediately)
		       CR  freight_receivable_account (cleared when customer SI is raised;
		                                       link the SI via sq.sales_invoice)

		Shipper billing, Collect  (carrier invoices after delivery; cost passed to customer)
		  → Purchase Invoice, item expense_account = freight_receivable_account
		       DR  freight_receivable_account (cleared when customer SI taxes-and-charges line posts)
		       CR  Accounts Payable — Carrier

		Shipper billing, Prepaid, no delivery_customer  (company absorbs; e.g. freight terminal)
		  → Purchase Invoice, item expense_account = freight_expense_account
		       DR  freight_expense_account
		       CR  Accounts Payable — Carrier
		"""
		shipment = frappe.get_doc("Shipment", self.shipment)

		if shipment.billing_type != "Shipper":
			return

		company = get_shipment_company_for_ltl(shipment)
		fc = get_freight_carrier_settings(company, shipment.preferred_carrier)

		if not fc or not fc.auto_create_accounting_entry:
			return

		if not fc.freight_item:
			frappe.msgprint(
				_(
					"No Freight Item is configured in Freight Carrier Settings for {0}. "
					"No accounting entry was created. Configure a Freight Item to enable "
					"automatic expense recognition."
				).format(shipment.preferred_carrier),
				alert=True,
			)
			return

		if shipment.payment_terms == "Prepaid" and shipment.get("delivery_customer"):
			self.create_freight_journal_entry(shipment, fc, company)
		elif shipment.payment_terms == "Collect":
			self.create_freight_purchase_invoice(
				shipment,
				fc,
				company,
				expense_account=fc.freight_receivable_account or fc.freight_expense_account,
			)
		else:
			self.create_freight_purchase_invoice(
				shipment,
				fc,
				company,
				expense_account=fc.freight_expense_account,
			)

	def create_freight_purchase_invoice(self, shipment, fc, company, expense_account=None) -> None:
		remarks = _("Freight for Shipment {0} — {1} {2}").format(
			self.shipment, self.carrier, self.service_level or ""
		)
		pi = frappe.new_doc("Purchase Invoice")
		pi.supplier = shipment.preferred_carrier
		pi.company = company
		pi.posting_date = today()
		pi.due_date = today()
		pi.remarks = remarks

		item_row = {
			"item_code": fc.freight_item,
			"qty": 1,
			"rate": self.grand_total,
			"description": remarks,
		}
		if expense_account:
			item_row["expense_account"] = expense_account

		pi.append("items", item_row)
		pi.flags.ignore_permissions = True
		pi.insert()
		pi.submit()
		self.db_set("purchase_invoice", pi.name)

	def create_freight_journal_entry(self, shipment, fc, company) -> None:
		if not fc.freight_expense_account or not fc.freight_receivable_account:
			frappe.msgprint(
				_(
					"Both Freight Expense Account and Freight Receivable Account must be set in "
					"Freight Carrier Settings for {0} to create a chargeback Journal Entry."
				).format(shipment.preferred_carrier),
				alert=True,
			)
			return

		remarks = _("Freight for Shipment {0} — {1} {2}").format(
			self.shipment, self.carrier, self.service_level or ""
		)
		jv = frappe.new_doc("Journal Entry")
		jv.voucher_type = "Journal Entry"
		jv.company = company
		jv.posting_date = today()
		jv.user_remark = remarks
		jv.append(
			"accounts",
			{
				"account": fc.freight_expense_account,
				"debit_in_account_currency": self.grand_total,
				"user_remark": remarks,
			},
		)
		jv.append(
			"accounts",
			{
				"account": fc.freight_receivable_account,
				"credit_in_account_currency": self.grand_total,
				"party_type": "Customer",
				"party": shipment.delivery_customer,
				"user_remark": remarks,
			},
		)
		jv.flags.ignore_permissions = True
		jv.insert()
		jv.submit()
		self.db_set("journal_entry", jv.name)

	def cancel_freight_accounting_entry(self) -> None:
		if self.purchase_invoice:
			pi = frappe.get_doc("Purchase Invoice", self.purchase_invoice)
			if pi.docstatus == 1:
				pi.flags.ignore_permissions = True
				pi.cancel()
			self.db_set("purchase_invoice", None)

		if self.get("journal_entry"):
			jv = frappe.get_doc("Journal Entry", self.journal_entry)
			if jv.docstatus == 1:
				jv.flags.ignore_permissions = True
				jv.cancel()
			self.db_set("journal_entry", None)


@frappe.whitelist()
def check_if_shipment_pickup_scheduled(doc: ShipmentQuotation | str) -> dict:
	doc = frappe._dict(json.loads(doc)) if isinstance(doc, str) else doc
	dt, dn = "Shipment", doc.shipment
	pu_scheduled = frappe.get_value(dt, dn, "pickup_id") or frappe.get_value(dt, dn, "awb_number")
	return {"pickup_scheduled": bool(pu_scheduled)}
