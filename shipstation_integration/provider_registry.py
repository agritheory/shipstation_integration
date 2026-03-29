# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import frappe
from .base_ltl import BaseLTL


def get_ltl_provider(doc=None, company: str | None = None, supplier: str | None = None) -> BaseLTL:
	"""Resolve the LTL provider class for a given Shipment doc or (company, supplier) pair.
	Resolution order:
	1. Check frappe.get_hooks("override_shipstation") for a provider dict keyed by an identifier derivable from Freight Carrier Settings (e.g. base_url domain).
	2. Fall back to ShipstationLTL as default.
	"""
	from .shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings import (
		get_freight_carrier_settings,
	)
	from .utils import get_shipment_company_for_ltl

	co = company or (get_shipment_company_for_ltl(doc) if doc else None)
	sup = supplier or (getattr(doc, "preferred_carrier", None) if doc else None)

	fc = get_freight_carrier_settings(co, sup) if co and sup else None

	# Resolve provider from hooks
	hook = frappe.get_hooks("override_shipstation") or {}
	provider_map = hook.get("ltl", {})

	if fc and provider_map:
		provider_key = _resolve_provider_key(fc, provider_map)
		if provider_key and provider_key in provider_map:
			return frappe.get_attr(provider_map[provider_key])()

	# Legacy single-class hook (existing behavior)
	if isinstance(provider_map, list) and provider_map:
		return frappe.get_attr(provider_map[-1])()

	from .ltl import ShipstationLTL

	return ShipstationLTL()


def _resolve_provider_key(fc, provider_map: dict) -> str | None:
	"""Determine provider key from Freight Carrier Settings.
	Strategy: match base_url against known provider domains.
	"""
	base_url = (fc.base_url or "").strip().lower()
	domain_map = {
		"shipengine.com": "shipstation",
		"shipstation.com": "shipstation",
		"wwex.com": "wwex",
		"banyantechnology.com": "banyan",
		"odfl.com": "odfl",
	}
	for domain, key in domain_map.items():
		if domain in base_url and key in provider_map:
			return key
	return None
