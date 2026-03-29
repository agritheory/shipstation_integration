# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
Shipment Billing — T-Account Test Suite
========================================

Four ways a shipment cost may flow through the books, keyed to
``Shipment.billing_type``, ``Shipment.payment_terms``, and whether a
``delivery_customer`` is set (i.e. the freight will be charged back).

All tests use real fixture data: Ambrosia Pie Company ships via Test LTL
Carrier.  The "Standard LTL" rate from the captured ShipEngine fixture is
$474.38.

Accounting document chosen at Shipment Quotation submit
-------------------------------------------------------

  Consignee billing
    → nothing: customer pays the carrier directly.

  Shipper / Prepaid / delivery_customer set  (order 21–23)
    → Journal Entry
    | Account                        | Stock Ledger |   Debit |   Credit | Party             |
    | ------------------------------ |:------------:| -------:| --------:| ----------------- |
    | Freight and Forwarding Charges |              | $474.38 |          |                   |
    | Freight Clearing               |              |         |  $474.38 | Almacs Food Group |
    Freight Clearing is a Current Asset account cleared when the customer SI
    taxes-and-charges line posts.

  Shipper / Collect  (order 20)
    → Purchase Invoice, item expense_account = freight_receivable_account
    | Account          | Stock Ledger |   Debit |   Credit | Party            |
    | ---------------- |:------------:| -------:| --------:| ---------------- |
    | Freight Clearing |              | $474.38 |          |                  |
    | Creditors        |              |         |  $474.38 | Test LTL Carrier |
    Freight Clearing cleared when customer SI taxes-and-charges line posts.

  Shipper / Prepaid / no delivery_customer  (company absorbs; order 43)
    → Purchase Invoice, item expense_account = freight_expense_account
    | Account                        | Stock Ledger |   Debit |   Credit | Party            |
    | ------------------------------ |:------------:| -------:| --------:| ---------------- |
    | Freight and Forwarding Charges |              | $474.38 |          |                  |
    | Creditors                      |              |         |  $474.38 | Test LTL Carrier |
    Used for freight-terminal shipments where no specific customer is billed.

  Scenario 3 — Pass-through; customer pays freight on Sales Invoice  (order 30 — TODO)
    SI taxes-and-charges line clears Freight Clearing set up in scenarios above.
    | Account          | Stock Ledger |   Debit |   Credit | Party             |
    | ---------------- |:------------:| -------:| --------:| ----------------- |
    | Debtors          |              | $474.38 |          | Almacs Food Group |
    | Freight Clearing |              |         |  $474.38 |                   |
    Net P&L: Freight Expense (from JE/PI) offset by Freight Clearing reversal = $0

  Scenario 4 — Prepaid + chargeback, full cycle  (order 35 — TODO)
    JE from scenario Prepaid+customer PLUS SI taxes-and-charges line.
"""

import frappe
import pytest
from frappe.utils.data import flt

from shipstation_integration.ltl import ShipstationLTL
from shipstation_integration.tests.setup import (
	get_draft_ltl_shipment_for_tests,
	get_freight_terminal_shipment_for_tests,
	ltl_quotes_response_for_tests,
	reset_ltl_shipment_quotation_test_state,
)


COMPANY = "Ambrosia Pie Company"
LTL_CARRIER = "Test LTL Carrier"
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


def freight_receivable_account():
	"""Freight Receivable account used for prepaid chargeback (Customer party)."""
	return frappe.db.get_value(
		"Account",
		{"account_name": "Freight Receivable", "company": COMPANY},
		"name",
	)


def creditors_account():
	return frappe.db.get_value(
		"Account",
		{"root_type": "Liability", "account_type": "Payable", "company": COMPANY, "is_group": 0},
		"name",
	)


@pytest.mark.order(20)
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


@pytest.mark.order(21)
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


@pytest.mark.order(22)
def test_prepaid_billing_journal_entry_gl_entries():
	"""
	Continued from order 21 — verify GL entries on the auto-created Journal Entry.

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


@pytest.mark.order(23)
def test_prepaid_billing_cancel_sq_cancels_journal_entry():
	"""
	Continued from order 21/22 — cancelling the SQ reverses the Journal Entry.

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


@pytest.mark.order(30)
@pytest.mark.skip(
	reason="TODO: implement Sales Invoice freight line creation and Consignee billing path"
)
def test_pass_through_freight_creates_sales_invoice_line():
	"""
	Scenario 3 — Customer pays freight; billed on their Sales Invoice.

	The company adds a Freight Service line to the customer's SI at the quoted
	rate.  Net P&L impact is zero: Freight Income offsets Freight Expense.

	| Account                          | Stock Ledger |   Debit |   Credit | Party              |
	| -------------------------------- |:------------:| -------:| --------:| ------------------ |
	| Debtors                          |              | $474.38 |          | Almacs Food Group  |
	| Freight Income                   |              |         |  $474.38 |                    |
	| Freight and Forwarding Charges   |              | $474.38 |          |                    |
	| Creditors                        |              |         |  $474.38 | Test LTL Carrier   |

	Net freight P&L = Freight Income − Freight Expense = $0
	"""


@pytest.mark.order(31)
@pytest.mark.skip(reason="TODO: implement Sales Invoice freight line creation")
def test_pass_through_freight_income_offsets_expense_gl():
	"""
	Scenario 3 continued — verify that Freight Income and Freight Expense
	accounts carry equal and opposite balances after the SI and PI post.

	Expected net effect on P&L: $0 (complete pass-through).
	"""


@pytest.mark.order(35)
@pytest.mark.skip(reason="TODO: implement after Scenarios 2 and 3 are fully wired up")
def test_prepaid_with_chargeback_creates_pi_and_si_freight_line():
	"""
	Scenario 4 — Company pre-pays carrier, then recovers cost from customer.

	Step 1: SQ submit → auto-PI (same as Scenario 2).
	Step 2: Freight Service line added to customer's Sales Invoice.
	Step 3: Net P&L = $0 after both documents post.

	| Account                          | Stock Ledger |   Debit |   Credit | Party              |
	| -------------------------------- |:------------:| -------:| --------:| ------------------ |
	| Freight and Forwarding Charges   |              | $474.38 |          |                    | ← PI (auto)
	| Creditors                        |              |         |  $474.38 | Test LTL Carrier   | ← PI (auto)
	| Debtors                          |              | $474.38 |          | Almacs Food Group  | ← SI line
	| Freight Income                   |              |         |  $474.38 |                    | ← SI line
	"""


@pytest.mark.order(36)
@pytest.mark.skip(reason="TODO: implement after Scenario 4 is wired up")
def test_prepaid_with_chargeback_net_freight_pl_is_zero():
	"""
	Scenario 4 continued — after PI and SI post, verify the net freight impact
	on the P&L is zero: Freight Income $474.38 − Freight Expense $474.38 = $0.
	"""


@pytest.mark.order(40)
def test_freight_terminal_shipment_has_contact_delivery_type():
	"""
	The Northeast Freight Terminal scenario: Ambrosia Pie ships to a carrier
	terminal where Cafe 27 Cafeteria's transport picks up the load.
	delivery_to_type = "Contact" routes to the terminal address/contact;
	accounting is otherwise identical to Scenario 2.
	"""
	shipment = get_freight_terminal_shipment_for_tests()
	assert shipment.delivery_to_type == "Contact"
	assert shipment.carrier_terminal_pickup == 1


@pytest.mark.order(41)
def test_freight_terminal_shipment_contact_has_phone_and_email():
	"""
	The terminal agent contact must have a phone and email so that the
	ShipEngine payload passes validation before the API call is made.
	"""
	shipment = get_freight_terminal_shipment_for_tests()
	assert shipment.shipping_contact, "Terminal contact must be set on the shipment"

	contact = frappe.get_doc("Contact", shipment.shipping_contact)
	phones = [p.phone for p in contact.phone_nos if p.phone]
	emails = [e.email_id for e in contact.email_ids if e.email_id]
	assert phones, f"Contact {contact.name} must have at least one phone number"
	assert emails, f"Contact {contact.name} must have at least one email address"


@pytest.mark.order(42)
def test_freight_terminal_shipment_sdn_rows_have_parcel_data():
	"""
	Every Shipment Delivery Note row must carry parcel_number, dimensions, and
	weight so that build_packages_from_sdn can construct a valid ShipEngine payload.
	"""
	shipment = get_freight_terminal_shipment_for_tests()
	assert shipment.shipment_delivery_note, "SDN rows must be present"
	for row in shipment.shipment_delivery_note:
		assert row.parcel_number, f"Row {row.idx} missing parcel_number"
		assert (
			row.parcel_length and row.parcel_width and row.parcel_height
		), f"Row {row.idx} missing parcel dimensions"
		assert row.parcel_weight, f"Row {row.idx} missing parcel_weight"


@pytest.mark.order(43)
def test_freight_terminal_prepaid_sq_submit_creates_purchase_invoice():
	"""
	Freight terminal — Shipper / Prepaid / no delivery_customer → company absorbs cost.

	Because there is no delivery_customer (goods go to a terminal, not directly to
	a billed customer), the SQ submit path creates a Purchase Invoice rather than a
	Journal Entry.  The item expense_account is freight_expense_account — the cost
	is a direct expense, not a receivable to be recovered.

	| Account                        | Stock Ledger |   Debit |   Credit | Party            |
	| ------------------------------ |:------------:| -------:| --------:| ---------------- |
	| Freight and Forwarding Charges |              | $474.38 |          |                  |
	| Creditors                      |              |         |  $474.38 | Test LTL Carrier |
	"""
	terminal_shipment = get_freight_terminal_shipment_for_tests()
	assert terminal_shipment.billing_type == "Shipper"
	assert terminal_shipment.payment_terms == "Prepaid"
	assert not terminal_shipment.get(
		"delivery_customer"
	), "Terminal shipment must have no delivery_customer to exercise the absorb-cost path"

	for sq_name in frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": terminal_shipment.name},
		pluck="name",
	):
		frappe.delete_doc("Shipment Quotation", sq_name, force=True)

	ltl = ShipstationLTL()
	ltl.save_ltl_quotes_as_shipment_quotations_and_display(
		terminal_shipment, ltl_quotes_response_for_tests()[:1]
	)
	sq = frappe.get_last_doc("Shipment Quotation", filters={"shipment": terminal_shipment.name})
	sq.submit()
	sq.reload()

	assert sq.docstatus == 1
	assert sq.purchase_invoice

	pi = frappe.get_doc("Purchase Invoice", sq.purchase_invoice)
	assert pi.docstatus == 1
	assert flt(pi.grand_total, 2) == LTL_STANDARD_RATE
	assert (
		pi.items[0].expense_account == freight_expense_account()
	), "Terminal shipment PI must use freight_expense_account, not the clearing account"

	gl_entries = frappe.get_all(
		"GL Entry",
		filters={"voucher_no": pi.name, "is_cancelled": 0},
		fields=["account", "debit", "credit"],
	)
	assert len(gl_entries) == 2

	expense_acct = freight_expense_account()
	gl_dr = next((g for g in gl_entries if g.account == expense_acct), None)
	assert gl_dr, f"Expected debit entry for {expense_acct}"
	assert flt(gl_dr.debit, 2) == LTL_STANDARD_RATE

	sq.cancel()
	for sq_name in frappe.get_all(
		"Shipment Quotation",
		filters={"shipment": terminal_shipment.name},
		pluck="name",
	):
		frappe.delete_doc("Shipment Quotation", sq_name, force=True)
	frappe.db.set_value(
		"Shipment",
		terminal_shipment.name,
		{"accepted_quotation": None, "shipment_amount": 0},
	)


@pytest.mark.order(44)
def test_freight_terminal_delivery_address_is_terminal_not_customer():
	"""
	The physical delivery address must be the freight terminal, not the customer.
	The customer's address is irrelevant to routing; only the terminal appears
	in the ShipEngine payload.
	"""
	shipment = get_freight_terminal_shipment_for_tests()
	assert shipment.delivery_address_name

	addr = frappe.get_doc("Address", shipment.delivery_address_name)
	assert "Terminal" in addr.address_title, f"Expected terminal address, got '{addr.address_title}'"
	customer_links = [lnk for lnk in addr.links if lnk.link_doctype == "Customer"]
	assert not customer_links, "Freight terminal address should not be linked to a Customer"
