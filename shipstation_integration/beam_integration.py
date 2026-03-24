# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""
BEAM integration for shipstation_integration.

When the BEAM app is installed this module:

  1. Registers each SSCC-18 as a BEAM ``Handling Unit`` so the GS1-128
     barcode is scannable through BEAM's scan dispatcher.
  2. Creates and submits a ``Repack`` Stock Entry on Packing Slip submission
     to record the HU transformation in the stock ledger.
  3. Updates ``Delivery Note Item.handling_unit`` so outbound SLEs reference
     the new SSCC HUs when the DN is eventually submitted.

BEAM's ``generate_handling_units`` hook (``before_submit`` on Stock Entry)
skips any row whose ``handling_unit`` is already set (line 67-68 in
beam/beam/beam/handling_unit.py).  We exploit that to pre-set our SSCC
codes as the HU on target rows so BEAM never overwrites them with
UUID-derived names.

Source-row constraint: BEAM's ``validate_items_with_handling_unit`` requires that every source row
in a Repack SE with ``Item.enable_handling_unit = 1`` carries a
``handling_unit`` value.  This requirement does not apply to the packing-slip
Repack flow: customers who use BEAM HUs exclusively for outbound packing
(SSCC labels) will have stock movements — receipts, transfers, pick — that
carry no HU.  The source rows in the Repack SE legitimately have no HU, and
BEAM's validation is suppressed via ``frappe.flags`` for this call only.
"""

import frappe
from frappe import _
from frappe.utils import flt, today


def is_beam_installed() -> bool:
	"""Return True if the BEAM app's Handling Unit doctype is present."""
	return bool(frappe.db.exists("DocType", "Handling Unit"))


def beam_handling_units_enabled(company: str) -> bool:
	"""Return True if BEAM has ``enable_handling_units`` set for *company*."""
	if not is_beam_installed():
		return False
	settings = frappe.db.get_value(
		"BEAM Settings",
		{"company": company},
		"enable_handling_units",
	)
	return bool(settings)


def psi_has_handling_unit_field() -> bool:
	"""Return True if BEAM's Inventory Dimension has been applied to Packing Slip Item."""
	return frappe.get_meta("Packing Slip Item").has_field("handling_unit")


def dni_has_handling_unit_field() -> bool:
	"""Return True if BEAM's Inventory Dimension has been applied to Delivery Note Item."""
	return frappe.get_meta("Delivery Note Item").has_field("handling_unit")


def create_handling_unit_for_sscc(sscc_code: str) -> str | None:
	"""
	Create a BEAM ``Handling Unit`` whose name equals the SSCC-18 code.

	By naming the HU with the SSCC, BEAM's barcode scan dispatcher resolves
	the scanned GS1-128 barcode directly to this Handling Unit via the
	auto-created ``Item Barcode`` child record (``barcode = self.name``).

	Returns the HU name (same as ``sscc_code``) or ``None`` if BEAM is not
	installed.  Idempotent — no-op if the HU already exists.
	"""
	if not is_beam_installed():
		return None

	if frappe.db.exists("Handling Unit", sscc_code):
		return sscc_code

	hu = frappe.new_doc("Handling Unit")
	# flags.name_set = True tells Frappe to skip its autoname call so
	# BEAM's autoname() (which generates a UUID-derived integer) is not
	# invoked and our SSCC is preserved as the document name.
	hu.flags.name_set = True
	hu.name = sscc_code
	hu.handling_unit_name = sscc_code
	hu.insert(ignore_permissions=True)

	return sscc_code


def on_packing_slip_submit(doc) -> None:
	"""
	Called from ``shipstation_integration.packing_slip.on_submit``.

	When BEAM handling units are enabled the sequence is:
	  1. Create and save the Repack SE as a draft (no HUs yet).
	  2. Create SSCC HUs (SE exists as their anchor).
	  3. Back-fill ``handling_unit`` on SE target rows, then submit.
	  4. Update PS item and DN item ``handling_unit`` fields.

	When BEAM is installed but HU tracking is disabled, SSCC HU documents
	are still created so the barcode remains scannable via BEAM's scan
	dispatcher, but no SE is written.
	"""
	if not is_beam_installed():
		return

	company = frappe.db.get_value("Delivery Note", doc.delivery_note, "company")

	if beam_handling_units_enabled(company):
		create_packing_slip_repack_entry(doc, company)
	else:
		for code in {item.ucc128 for item in doc.items if item.ucc128}:
			create_handling_unit_for_sscc(code)

	update_dn_item_handling_units(doc)


def create_packing_slip_repack_entry(doc, company: str) -> str | None:
	"""
	Build and submit a ``Repack`` Stock Entry that records the HU
	transformation for this Packing Slip in the stock ledger.

	Structure — source rows (s_warehouse only)
	    One row per unique ``(item_code, warehouse, source_hu)`` combination
	    derived from the linked Delivery Note items.  Rows without a source
	    HU are included only when ``Item.enable_handling_unit = 0``; items
	    with HU tracking enabled but no source HU are skipped to avoid
	    BEAM's ``validate_items_with_handling_unit`` error — callers must
	    ensure such items were received with BEAM enabled before submitting.

	Target rows (t_warehouse only)
	    One row per packed Packing Slip Item.  ``handling_unit`` is NOT set
	    during the initial ``save()`` so Frappe's link validator doesn't
	    reject not-yet-existing HU documents.  After the SE draft is saved
	    and SSCC HUs are created, ``handling_unit`` is set **on the in-memory
	    row object** before ``se.submit()`` is called.  This is critical:
	    ``se.submit()`` → ``se.save()`` re-persists the in-memory doc, so any
	    ``frappe.db.set_value`` written between the two ``save()`` calls would
	    be immediately overwritten.  BEAM's ``generate_handling_units`` hook
	    (``before_submit``) checks ``row.get("handling_unit")`` on the same
	    in-memory doc and skips UUID generation when the field is already set.
	    Rows without an SSCC keep ``is_finished_item = 1`` so BEAM generates
	    a UUID-derived HU for them as a fallback.

	Returns the submitted SE name, or ``None`` if there are no packed items.
	"""
	packed = [item for item in doc.items if item.parcel_number and item.ucc128]
	if not packed:
		return None

	has_hu_on_dni = dni_has_handling_unit_field()
	has_hu_on_psi = psi_has_handling_unit_field()

	# Tracks item_codes whose source rows have zero rate (not free items) so
	# matching target rows can also get allow_zero_valuation_rate = 1.
	zero_rate_items: set[str] = set()

	source_map: dict[tuple, dict] = {}
	for ps_item in packed:
		if not ps_item.dn_detail:
			continue

		dn_item = frappe.db.get_value(
			"Delivery Note Item",
			ps_item.dn_detail,
			[
				"item_code",
				"warehouse",
				"uom",
				"conversion_factor",
				"handling_unit",
				"incoming_rate",
				"is_free_item",
			],
			as_dict=True,
		)
		if not dn_item or not dn_item.warehouse:
			continue

		source_hu = dn_item.handling_unit if has_hu_on_dni else None

		basic_rate = flt(dn_item.incoming_rate)
		allow_zero = 1 if dn_item.is_free_item else 0

		if not basic_rate and not dn_item.is_free_item:
			# incoming_rate on the DN row may be 0 when the item was received
			# before a valuation method was configured or when the DN was created
			# manually.  Use the same SLE-based lookup ERPNext uses internally so
			# FIFO/LIFO queues and Moving Average are handled correctly.
			from erpnext.stock.utils import get_incoming_rate

			basic_rate = flt(
				get_incoming_rate(
					{
						"item_code": ps_item.item_code,
						"warehouse": dn_item.warehouse,
						"posting_date": today(),
						"posting_time": frappe.utils.now_datetime().strftime("%H:%M:%S"),
						"qty": -1 * flt(ps_item.qty),
						"voucher_type": "Stock Entry",
						"voucher_no": "",
						"company": company,
					},
					raise_error_if_no_rate=False,
				)
			)
			if not basic_rate:
				# Truly zero-value stock — acknowledge it explicitly so ERPNext
				# does not raise "Valuation Rate is required".
				allow_zero = 1
				zero_rate_items.add(ps_item.item_code)

		key = (ps_item.item_code, dn_item.warehouse, source_hu or "")
		if key not in source_map:
			source_map[key] = {
				"item_code": ps_item.item_code,
				"s_warehouse": dn_item.warehouse,
				"handling_unit": source_hu,
				"uom": dn_item.uom or ps_item.stock_uom,
				"conversion_factor": flt(dn_item.conversion_factor) or 1.0,
				"basic_rate": basic_rate,
				"allow_zero_valuation_rate": allow_zero,
				"qty": 0.0,
			}
		source_map[key]["qty"] += flt(ps_item.qty)

	# Derive a canonical warehouse for target rows (same as sources).
	# Fall back to the first DN item's warehouse if source_map is empty.
	if source_map:
		target_warehouse = next(iter(source_map.values()))["s_warehouse"]
	elif packed[0].dn_detail:
		target_warehouse = frappe.db.get_value("Delivery Note Item", packed[0].dn_detail, "warehouse")
	else:
		# Cannot determine warehouse — cannot create SE.
		frappe.log_error(
			title="Packing Slip Repack SE skipped",
			message=f"No warehouse found for PS {doc.name}; no Repack SE created.",
		)
		return None

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Repack"
	se.purpose = "Repack"
	se.company = company
	se.posting_date = today()
	se.posting_time = frappe.utils.now_datetime().strftime("%H:%M:%S")
	se.remarks = _("Repack for Packing Slip {0}").format(doc.name)

	# Source rows.  The source HU (if any) comes from the DNI — gate on
	# whether DNI actually carries the field, not on has_hu_on_psi.
	for src in source_map.values():
		row = se.append(
			"items",
			{
				"item_code": src["item_code"],
				"s_warehouse": src["s_warehouse"],
				"qty": src["qty"],
				"uom": src["uom"],
				"conversion_factor": src["conversion_factor"],
				"basic_rate": src["basic_rate"],
				"allow_zero_valuation_rate": src["allow_zero_valuation_rate"],
			},
		)
		if src["handling_unit"]:
			row.handling_unit = src["handling_unit"]

	# Target rows — handling_unit intentionally left unset during the initial
	# se.save() so Frappe's link validator doesn't fail against HU docs that
	# don't exist yet.  Track (row, sscc) pairs; SSCC rows get their HU set
	# on the in-memory object in Step 3 so BEAM's generate_handling_units
	# hook sees the field already populated and skips UUID generation.
	# Rows that have no SSCC (no parcel code) keep is_finished_item=1 so
	# BEAM generates a UUID HU for them as a fallback.
	target_rows: list[tuple] = []  # (frappe row object, sscc_code or "")
	for ps_item in packed:
		target_item: dict = {
			"item_code": ps_item.item_code,
			"t_warehouse": target_warehouse,
			"qty": flt(ps_item.qty),
			"uom": ps_item.stock_uom,
			"conversion_factor": 1.0,
		}
		# When the source side has zero rate, ERPNext will compute a zero cost
		# for this target row too.  Mark it explicitly so validation passes.
		if ps_item.item_code in zero_rate_items:
			target_item["allow_zero_valuation_rate"] = 1

		row = se.append("items", target_item)
		if ps_item.ucc128:
			target_rows.append((row, ps_item.ucc128))
		else:
			row.is_finished_item = 1
			target_rows.append((row, ""))

	# BEAM's validate_items_with_handling_unit runs on save() and submit(). This Repack
	# is saved first without target handling_unit (SSCC HUs do not exist yet), and
	# source rows may have no HU when DN items are not HU-tracked — set the flag for
	# the whole save → HU create → set row.handling_unit → submit sequence.
	frappe.flags.beam_allow_source_rows_without_hu = True
	try:
		# Step 1: persist the SE as a draft — no HUs required yet.
		se.save(ignore_permissions=True)

		# Step 2: create SSCC HUs now that the SE draft exists as their anchor.
		for _row, sscc in target_rows:
			if sscc:
				create_handling_unit_for_sscc(sscc)

		# Step 3: set handling_unit on the IN-MEMORY row objects before submit.
		# Must update the in-memory object (not just the DB) because se.submit()
		# calls se.save() again from the in-memory doc, which would overwrite any
		# frappe.db.set_value changes made here.  BEAM's generate_handling_units
		# hook (before_submit) checks row.get("handling_unit") on the in-memory
		# doc — setting it here means BEAM sees the SSCC and skips UUID generation.
		for row, sscc in target_rows:
			if sscc:
				row.handling_unit = sscc

		se.submit()
	finally:
		frappe.flags.beam_allow_source_rows_without_hu = False

	# Step 4: back-link SE to PS; update PS item handling_unit fields when the
	# Packing Slip Item doctype carries BEAM's inventory dimension.
	frappe.db.set_value("Packing Slip", doc.name, "repack_stock_entry", se.name)

	if has_hu_on_psi:
		for _row, sscc in target_rows:
			if sscc:
				for ps_item in packed:
					if ps_item.ucc128 == sscc:
						frappe.db.set_value(
							"Packing Slip Item", ps_item.name, "handling_unit", sscc, update_modified=False
						)

	return se.name


def update_dn_item_handling_units(doc) -> None:
	"""
	Update ``Delivery Note Item.handling_unit`` to the new SSCC HU for each
	DN item whose entire PS quantity lands in a single parcel.

	When a DN item is split across multiple parcels the update is skipped for
	that item — the stock ledger relationship is captured by the Repack SE
	instead.
	"""
	if not dni_has_handling_unit_field():
		return

	# Map each dn_detail → set of (parcel_number, ucc128) pairs it appears in.
	dn_detail_parcels: dict[str, set[tuple]] = {}
	for item in doc.items:
		if not item.dn_detail or not item.parcel_number or not item.ucc128:
			continue
		dn_detail_parcels.setdefault(item.dn_detail, set()).add((item.parcel_number, item.ucc128))

	for dn_detail, parcel_set in dn_detail_parcels.items():
		parcel_numbers = {p[0] for p in parcel_set}
		if len(parcel_numbers) == 1:
			# All qty for this DN item is in one parcel — safe to set the HU.
			sscc = next(iter(parcel_set))[1]
			frappe.db.set_value("Delivery Note Item", dn_detail, "handling_unit", sscc)


def on_shipment_submit(doc) -> None:
	"""
	Called from ``shipstation_integration.shipment_pack.on_submit``.

	Mirrors the Packing Slip flow but operates on Shipment Delivery Note rows
	instead of Packing Slip Item rows.  When BEAM handling units are enabled:
	  1. Create and save the Repack SE as a draft.
	  2. Create SSCC HUs.
	  3. Back-fill handling_unit on SE target rows, then submit.
	  4. Update DN item handling_unit fields.

	When BEAM is installed but HU tracking is disabled, SSCC HU documents are
	still created so barcodes remain scannable.
	"""
	if not is_beam_installed():
		return

	packed_rows = [
		row for row in (doc.shipment_delivery_note or []) if row.parcel_number and row.ucc128
	]
	if not packed_rows:
		return

	# Determine company from the Shipment's own company field
	company = doc.company

	if beam_handling_units_enabled(company):
		create_shipment_repack_entry(doc, company)
	else:
		for code in {row.ucc128 for row in packed_rows if row.ucc128}:
			create_handling_unit_for_sscc(code)

	update_sdn_dn_item_handling_units(doc)


def create_shipment_repack_entry(doc, company: str) -> str | None:
	"""
	Build and submit a Repack Stock Entry from Shipment Delivery Note rows.

	Source rows come from the DN items referenced by ``dn_detail`` on each
	SDN row.  Target rows are one per packed SDN row, with SSCC codes
	pre-set on in-memory row objects so BEAM's generate_handling_units hook
	skips UUID generation.

	Returns the submitted SE name, or None if no packed rows with SSCC codes.
	"""
	packed = [row for row in (doc.shipment_delivery_note or []) if row.parcel_number and row.ucc128]
	if not packed:
		return None

	has_hu_on_dni = dni_has_handling_unit_field()

	zero_rate_items: set[str] = set()
	source_map: dict[tuple, dict] = {}

	for sdn_row in packed:
		if not sdn_row.dn_detail:
			continue

		dn_item = frappe.db.get_value(
			"Delivery Note Item",
			sdn_row.dn_detail,
			[
				"item_code",
				"warehouse",
				"uom",
				"conversion_factor",
				"handling_unit",
				"incoming_rate",
				"is_free_item",
			],
			as_dict=True,
		)
		if not dn_item or not dn_item.warehouse:
			continue

		source_hu = dn_item.handling_unit if has_hu_on_dni else None

		basic_rate = flt(dn_item.incoming_rate)
		allow_zero = 1 if dn_item.is_free_item else 0

		if not basic_rate and not dn_item.is_free_item:
			from erpnext.stock.utils import get_incoming_rate

			basic_rate = flt(
				get_incoming_rate(
					{
						"item_code": sdn_row.item_code,
						"warehouse": dn_item.warehouse,
						"posting_date": today(),
						"posting_time": frappe.utils.now_datetime().strftime("%H:%M:%S"),
						"qty": -1 * flt(sdn_row.qty),
						"voucher_type": "Stock Entry",
						"voucher_no": "",
						"company": company,
					},
					raise_error_if_no_rate=False,
				)
			)
			if not basic_rate:
				allow_zero = 1
				zero_rate_items.add(sdn_row.item_code)

		key = (sdn_row.item_code, dn_item.warehouse, source_hu or "")
		if key not in source_map:
			source_map[key] = {
				"item_code": sdn_row.item_code,
				"s_warehouse": dn_item.warehouse,
				"handling_unit": source_hu,
				"uom": dn_item.uom or sdn_row.stock_uom,
				"conversion_factor": flt(dn_item.conversion_factor) or 1.0,
				"basic_rate": basic_rate,
				"allow_zero_valuation_rate": allow_zero,
				"qty": 0.0,
			}
		source_map[key]["qty"] += flt(sdn_row.qty)

	if source_map:
		target_warehouse = next(iter(source_map.values()))["s_warehouse"]
	elif packed[0].dn_detail:
		target_warehouse = frappe.db.get_value("Delivery Note Item", packed[0].dn_detail, "warehouse")
	else:
		frappe.log_error(
			title="Shipment Repack SE skipped",
			message=f"No warehouse found for Shipment {doc.name}; no Repack SE created.",
		)
		return None

	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Repack"
	se.purpose = "Repack"
	se.company = company
	se.posting_date = today()
	se.posting_time = frappe.utils.now_datetime().strftime("%H:%M:%S")
	se.remarks = _("Repack for Shipment {0}").format(doc.name)

	for src in source_map.values():
		row = se.append(
			"items",
			{
				"item_code": src["item_code"],
				"s_warehouse": src["s_warehouse"],
				"qty": src["qty"],
				"uom": src["uom"],
				"conversion_factor": src["conversion_factor"],
				"basic_rate": src["basic_rate"],
				"allow_zero_valuation_rate": src["allow_zero_valuation_rate"],
			},
		)
		if src["handling_unit"]:
			row.handling_unit = src["handling_unit"]

	target_rows: list[tuple] = []
	for sdn_row in packed:
		target_item: dict = {
			"item_code": sdn_row.item_code,
			"t_warehouse": target_warehouse,
			"qty": flt(sdn_row.qty),
			"uom": sdn_row.stock_uom,
			"conversion_factor": 1.0,
		}
		if sdn_row.item_code in zero_rate_items:
			target_item["allow_zero_valuation_rate"] = 1

		row = se.append("items", target_item)
		if sdn_row.ucc128:
			target_rows.append((row, sdn_row.ucc128))
		else:
			row.is_finished_item = 1
			target_rows.append((row, ""))

	frappe.flags.beam_allow_source_rows_without_hu = True
	try:
		se.save(ignore_permissions=True)

		for _row, sscc in target_rows:
			if sscc:
				create_handling_unit_for_sscc(sscc)

		for row, sscc in target_rows:
			if sscc:
				row.handling_unit = sscc

		se.submit()
	finally:
		frappe.flags.beam_allow_source_rows_without_hu = False

	frappe.db.set_value("Shipment", doc.name, "repack_stock_entry", se.name)

	return se.name


def update_sdn_dn_item_handling_units(doc) -> None:
	"""
	Update Delivery Note Item.handling_unit to the SSCC HU for each DN item
	whose entire Shipment quantity lands in a single parcel.
	"""
	if not dni_has_handling_unit_field():
		return

	dn_detail_parcels: dict[str, set[tuple]] = {}
	for row in doc.shipment_delivery_note or []:
		if not row.dn_detail or not row.parcel_number or not row.ucc128:
			continue
		dn_detail_parcels.setdefault(row.dn_detail, set()).add((row.parcel_number, row.ucc128))

	for dn_detail, parcel_set in dn_detail_parcels.items():
		parcel_numbers = {p[0] for p in parcel_set}
		if len(parcel_numbers) == 1:
			sscc = next(iter(parcel_set))[1]
			frappe.db.set_value("Delivery Note Item", dn_detail, "handling_unit", sscc)


@frappe.whitelist()
def get_source_handling_units(packing_slip: str) -> dict:
	"""
	Return a mapping of ``{ps_item_name: source_handling_unit}`` for each
	Packing Slip Item whose linked Delivery Note Item carries a handling_unit.

	Used by the frontend to surface which DN handling units are being consumed
	(repacked) when items are assigned to parcels.

	Returns an empty dict if BEAM is not installed or no source HUs exist.
	"""
	if not is_beam_installed() or not dni_has_handling_unit_field():
		return {}

	ps = frappe.get_doc("Packing Slip", packing_slip)
	result: dict[str, str] = {}

	dn_details = [item.dn_detail for item in ps.items if item.dn_detail]
	if not dn_details:
		return result

	rows = frappe.get_all(
		"Delivery Note Item",
		filters={"name": ("in", dn_details)},
		fields=["name", "handling_unit"],
	)
	dn_hu_map = {r.name: r.handling_unit for r in rows if r.handling_unit}

	for item in ps.items:
		if item.dn_detail and item.dn_detail in dn_hu_map:
			result[item.name] = dn_hu_map[item.dn_detail]

	return result
