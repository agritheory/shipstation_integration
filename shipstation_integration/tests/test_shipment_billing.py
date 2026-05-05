# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Shipment Billing — T-Account Test Suite
========================================

Tests cover Collect (PI + clearing) and Prepaid chargeback (Journal Entry + GL)
paths for the seeded LTL shipment and Test LTL Carrier.  Standard rate from the
ShipEngine fixture is $474.38.

Accounting document chosen at Shipment Quotation submit
-------------------------------------------------------

  Consignee billing
    → nothing: customer pays the carrier directly.

  Shipper / Prepaid / delivery_customer set  (order 111–113)
    → Journal Entry
    | Account                        | Stock Ledger |   Debit |   Credit | Party             |
    | ------------------------------ |:------------:| -------:| --------:| ----------------- |
    | Freight and Forwarding Charges |              | $474.38 |          |                   |
    | Freight Clearing               |              |         |  $474.38 | Almacs Food Group |
    Freight Clearing is a Current Asset account cleared when the customer SI
    taxes-and-charges line posts.

  Shipper / Collect  (order 110)
    → Purchase Invoice, item expense_account = freight_clearing_account
    | Account          | Stock Ledger |   Debit |   Credit | Party            |
    | ---------------- |:------------:| -------:| --------:| ---------------- |
    | Freight Clearing |              | $474.38 |          |                  |
    | Creditors        |              |         |  $474.38 | Test LTL Carrier |
    Freight Clearing cleared when customer SI taxes-and-charges line posts.

"""

import frappe
import pytest
from frappe.utils.data import flt

from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	ltl_quotes_response_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


COMPANY = "Ambrosia Pie Company"
LTL_STANDARD_RATE = 474.38  # from captured ShipEngine fixture


def freight_expense_account():
	return frappe.db.get_value(
		"Account",
		{"account_name": "Freight and Forwarding Charges", "company": COMPANY},
		"name",
	)


def freight_clearing_account():
	"""Freight Clearing account used for both Collect PI and Prepaid JE chargeback."""
	return frappe.db.get_value(
		"Account",
		{"account_name": "Freight Clearing", "company": COMPANY},
		"name",
	)


@pytest.mark.order(110)
def test_collect_billing_sq_submit_creates_purchase_invoice_with_clearing_account():
	"""
	Shipper / Collect — carrier invoices after delivery; cost will be passed to customer via SI.

	A Purchase Invoice is created at SQ submit time so the liability to the carrier
	is recognised immediately.  The item expense_account is set to the Freight
	Clearing account (an asset clearing account) rather than Freight Expense,
	because the cost will be recovered from the customer when the SI is raised.

	| Account          | Stock Ledger |   Debit |   Credit | Party            |
	| ---------------- |:------------:| -------:| --------:| ---------------- |
	| Freight Clearing |              | $474.38 |          |                  |
	| Creditors        |              |         |  $474.38 | Test LTL Carrier |
	"""
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	original_payment_terms = ltl_shipment.payment_terms

	frappe.db.set_value("Shipment", ltl_shipment.name, "payment_terms", "Collect")

	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, ltl_quotes_response_for_tests()[:1]
	)
	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.submit()
	sq.reload()

	assert sq.purchase_invoice, "PI must be created for Collect billing"
	assert not sq.get("journal_entry"), "JE must not be created for Collect billing"

	pi = frappe.get_doc("Purchase Invoice", sq.purchase_invoice)
	assert pi.docstatus == 1
	assert flt(pi.grand_total, 2) == LTL_STANDARD_RATE
	assert pi.items[0].expense_account == freight_clearing_account()

	gl_entries = frappe.get_all(
		"GL Entry",
		filters={"voucher_no": pi.name, "is_cancelled": 0},
		fields=["account", "debit", "credit"],
	)
	assert len(gl_entries) == 2
	clearing_acct = freight_clearing_account()
	gl_dr = next((g for g in gl_entries if g.account == clearing_acct), None)
	assert gl_dr, f"Expected debit on {clearing_acct}"
	assert flt(gl_dr.debit, 2) == LTL_STANDARD_RATE

	sq.cancel()
	frappe.db.set_value("Shipment", ltl_shipment.name, "payment_terms", original_payment_terms)


@pytest.mark.order(111)
def test_prepaid_billing_with_customer_sq_submit_creates_journal_entry():
	"""
	Shipper / Prepaid / delivery_customer set — company pre-pays, charges back to customer.

	Submitting the Shipment Quotation creates a Journal Entry (not a PI) because
	the cost will be recovered via a Sales Invoice taxes-and-charges line.

	| Account                        | Stock Ledger |   Debit |   Credit | Party             |
	| ------------------------------ |:------------:| -------:| --------:| ----------------- |
	| Freight and Forwarding Charges |              | $474.38 |          |                   |
	| Freight Clearing               |              |         |  $474.38 | Almacs Food Group |
	"""
	reset_ltl_shipment_quotation_test_state()
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	assert ltl_shipment.billing_type == "Shipper"
	assert ltl_shipment.payment_terms == "Prepaid"
	assert ltl_shipment.delivery_customer, "LTL test shipment must have a delivery_customer"

	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		ltl_shipment, ltl_quotes_response_for_tests()[:1]
	)
	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": ltl_shipment.name})
	sq.submit()
	sq.reload()

	assert sq.docstatus == 1
	assert sq.journal_entry, "SQ submit must set sq.journal_entry"
	assert not sq.purchase_invoice, "No PI should be created for prepaid chargeback"

	ltl_shipment.reload()
	assert ltl_shipment.accepted_quotation == sq.name
	assert flt(ltl_shipment.shipment_amount, 2) == LTL_STANDARD_RATE


@pytest.mark.order(112)
def test_prepaid_billing_journal_entry_gl_entries():
	"""
	Continued from order 111 — verify GL entries on the auto-created Journal Entry.

	| Account                        | Stock Ledger |   Debit |   Credit | Party             |
	| ------------------------------ |:------------:| -------:| --------:| ----------------- |
	| Freight and Forwarding Charges |              | $474.38 |          |                   |
	| Freight Clearing               |              |         |  $474.38 | Almacs Food Group |
	"""
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	sq = frappe.get_last_doc(
		"Shipment Quotation",
		filters={"shipment": ltl_shipment.name, "docstatus": 1},
	)
	jv = frappe.get_doc("Journal Entry", sq.journal_entry)
	assert jv.docstatus == 1

	gl_entries = frappe.get_all(
		"GL Entry",
		filters={"voucher_no": jv.name, "is_cancelled": 0},
		fields=["account", "debit", "credit", "party_type", "party"],
	)
	assert len(gl_entries) == 2, f"Expected 2 GL entries, got {len(gl_entries)}"

	expense_acct = freight_expense_account()
	clearing_acct = freight_clearing_account()
	assert expense_acct, "Freight and Forwarding Charges account must exist"
	assert clearing_acct, "Freight Clearing account must exist"

	gl_dr = next((g for g in gl_entries if g.account == expense_acct), None)
	assert gl_dr is not None, f"Expected debit entry for {expense_acct}"
	assert flt(gl_dr.debit, 2) == LTL_STANDARD_RATE
	assert flt(gl_dr.credit, 2) == 0

	gl_cr = next((g for g in gl_entries if g.account == clearing_acct), None)
	assert gl_cr is not None, f"Expected credit entry for {clearing_acct}"
	assert flt(gl_cr.credit, 2) == LTL_STANDARD_RATE
	assert flt(gl_cr.debit, 2) == 0
	assert gl_cr.party_type == "Customer"
	assert gl_cr.party == ltl_shipment.delivery_customer


@pytest.mark.order(113)
def test_prepaid_billing_cancel_sq_cancels_journal_entry():
	"""
	Continued from order 111/112 — cancelling the SQ reverses the Journal Entry.

	| Account                        | Stock Ledger |   Debit |   Credit | Party             |
	| ------------------------------ |:------------:| -------:| --------:| ----------------- |
	| Freight and Forwarding Charges |              |         |  $474.38 |                   | ← reversal
	| Freight Clearing               |              | $474.38 |          | Almacs Food Group | ← reversal
	"""
	ltl_shipment = get_draft_ltl_shipment_for_tests()
	sq = frappe.get_last_doc(
		"Shipment Quotation",
		filters={"shipment": ltl_shipment.name, "docstatus": 1},
	)
	jv_name = sq.journal_entry

	sq.cancel()
	sq.reload()

	assert sq.docstatus == 2
	assert not sq.get("journal_entry"), "sq.journal_entry must be cleared on cancel"

	jv = frappe.get_doc("Journal Entry", jv_name)
	assert jv.docstatus == 2, "Journal Entry must be cancelled (docstatus=2)"

	ltl_shipment.reload()
	assert not ltl_shipment.accepted_quotation
	assert not ltl_shipment.shipment_amount

	reset_ltl_shipment_quotation_test_state()
