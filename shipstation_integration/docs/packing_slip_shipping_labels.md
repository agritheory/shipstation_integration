<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Packing Slip Shipping Labels – Feature Overview

<div class="byline">
  AgriTheory 2026-03-05
</div>

## Purpose

This feature enables **direct shipping label purchase and rate comparison from a Packing Slip** using the ShipStation API v2. Labels are issued per physical parcel, tracking numbers are written back to the Packing Slip, and SSCC-18 barcodes can be generated for GS1-compliant carton labeling.

---

## Where This Applies

* **Packing Slip** — rate shopping, label purchase, SSCC generation, and submit validation
* **Packing Slip Item** (child table) — stores parcel dimensions, tracking number, label URL, and UCC-128 code per row
* **Shipstation Settings** — stores the GS1 Company Prefix used for SSCC generation

---

## Workflow Overview

1. Create a Packing Slip linked to a Delivery Note.
2. Assign items to parcels by setting a **Parcel #** and entering parcel dimensions on each item row.
3. Optionally click **Get Rates** to compare carrier prices before purchasing.
4. Click **Purchase Label** (or call `create_label_for_packing_slip` directly) to buy a label from ShipStation.
5. Tracking numbers and label download URLs are written back to the corresponding item rows automatically.
6. Optionally click **Generate SSCC** to assign GS1-compliant SSCC-18 barcodes to each parcel before submission.
7. Submit the Packing Slip. Submission validates that all items have a parcel assignment and registers BEAM Handling Units.

---

## Parcel Assignment

Each **Packing Slip Item** row carries a set of per-parcel fields:

| Field | Purpose |
|---|---|
| `parcel_number` | Groups item rows into a physical box (integer, required before submit) |
| `parcel_length` / `parcel_width` / `parcel_height` | Box dimensions |
| `dimension_uom` | Unit for dimensions (e.g. `Inch`, `Centimeter`) |
| `parcel_weight` | Box weight |
| `parcel_weight_uom` | Unit for weight (e.g. `Pound`, `Kg`) |
| `tracking_number` | Written back after label purchase |
| `tracking_url` | Carrier-specific tracking link written back after label purchase |
| `label_url` | ShipStation label download URL written back after label purchase |
| `ucc128` | SSCC-18 barcode value (set via Generate SSCC or on submit) |

All items that share the same `parcel_number` belong to the same physical box and receive the same SSCC and tracking number.

---

## Rate Shopping

`get_rates_for_packing_slip(packing_slip)` fetches rate quotes from all configured ShipStation carriers before a label is purchased.

* Addresses come from `shipping_address_name` (ship-to) and `dispatch_address_name` (ship-from) on the Packing Slip.
* Package dimensions are sourced from the first item row that has a `parcel_number` set.
* Returns a list of rates sorted by price, each containing `rate_id`, `carrier_code`, `service_type`, `shipping_amount`, and `delivery_days`.
* The `rate_id` from any returned rate can be passed directly to `create_label_for_packing_slip` to skip a second API call.

**Validation:** All item rows must have a `parcel_number` set before rates can be fetched. An error is thrown otherwise.

---

## Label Purchase

`create_label_for_packing_slip(packing_slip, rate_id, carrier_id, service_code, force)` purchases one label per unique `parcel_number`.

### Arguments

| Argument | Required | Description |
|---|---|---|
| `packing_slip` | Yes | Name of the Packing Slip document |
| `rate_id` | Conditional | Rate ID from a prior rate request. Sufficient for single-parcel shipments. |
| `carrier_id` | Conditional | ShipEngine carrier ID (e.g. `se-123456`). Required if `rate_id` is not provided. |
| `service_code` | Conditional | Carrier service code (e.g. `usps_priority_mail`). Required alongside `carrier_id`. |
| `force` | No | Set to `True` to re-purchase a label even if tracking numbers already exist. Defaults to `False`. |

### What Happens

1. The function validates that shipping and dispatch addresses are set.
2. It checks for existing tracking numbers (duplicate guard — see below).
3. For each unique `parcel_number`, it builds a shipment payload from the item's parcel dimensions and purchases a label.
4. The label PDF is downloaded and attached to the Packing Slip under **Home/Shipstation Labels** (private).
5. `tracking_number`, `tracking_url`, and `label_url` are written to every item row belonging to that parcel.

### Carrier-Specific Tracking URLs

Tracking URLs are built automatically based on the carrier code returned by ShipStation:

| Carrier | Tracking URL Format |
|---|---|
| USPS | `https://tools.usps.com/go/TrackConfirmAction?tLabels={tracking_number}` |
| UPS | `https://www.ups.com/track?tracknum={tracking_number}` |
| FedEx | `https://www.fedex.com/fedextrack/?trknbr={tracking_number}` |
| DHL | `https://www.dhl.com/en/express/tracking.html?AWB={tracking_number}` |

### Return Value

A list of label response dicts — one per parcel — each containing:

```
{
    "label_id": "se-test-label-123",
    "tracking_number": "9400111899223100001234",
    "carrier_code": "usps",
    "service_code": "usps_priority_mail",
    "label_download": "https://...",
    "parcel_number": 1,
    "attached_file": "Packing Slip-00001_shipstation_api.pdf"
}
```

---

## Duplicate / Idempotency Guard

If any item row on the Packing Slip already has a `tracking_number`, calling `create_label_for_packing_slip` without `force=True` raises a `frappe.DuplicateEntryError` with the existing tracking number in the message.

This prevents accidental double-purchasing of labels. Pass `force=True` only when a label needs to be explicitly voided and repurchased.

To inspect the existing label info without triggering an error, call:

```python
from shipstation_integration.labels import get_existing_label_info
info = get_existing_label_info(packing_slip_doc)
# Returns: {"tracking_number": ..., "tracking_url": ..., "label_url": ..., "carrier": ...}
# Returns None if no tracking number exists yet.
```

---

## SSCC-18 Generation

SSCC (Serial Shipping Container Code) is the GS1 standard identifier for individual cartons. This feature generates valid 18-digit SSCC codes for each parcel.

### Prerequisites

* The **GS1 Company Prefix** (7–10 digits, issued by GS1) must be configured in **Shipstation Settings**.
* Items must be assigned to parcels via `parcel_number`.

### UI Button — Generate SSCC

Clicking **Generate SSCC** calls `generate_packing_slip_sscc(packing_slip)`:

* Items are grouped by `parcel_number`.
* One SSCC-18 is issued per unique parcel.
* The code is written to `ucc128` on every item row in that parcel.
* Parcels that already have a `ucc128` value are skipped (safe to call multiple times).
* Consumed serial numbers from `tabSeries` are never reused, even if the Packing Slip is later deleted — this guarantees global uniqueness within the GS1 prefix.

Returns a summary dict:
```
{"generated": 2, "skipped": 0, "codes": [{"name": "row-name", "ucc128": "003456789000000012"}]}
```

### Automatic Assignment on Submit

If any packed parcel still lacks an SSCC when the Packing Slip is submitted, `assign_sscc_codes` is called automatically during `before_submit` and assigns codes to the remaining parcels.

### SSCC-18 Structure

```
Extension digit (1) + GS1 Company Prefix (7–10) + Serial (16 – prefix_len) + Check digit (1) = 18 digits
```

The check digit is computed using the GS1 mod-10 algorithm.

---

## Submit Validation

`before_submit` raises a validation error if **no items have a `parcel_number` assigned**. Every item must be packed into at least one parcel before the Packing Slip can be submitted.

---

## BEAM Integration on Submit

When BEAM is installed, `on_submit` calls `on_packing_slip_submit` which:

1. Registers a **BEAM Handling Unit** for each SSCC code.
2. Creates and submits a **Repack Stock Entry** to record the handling unit transformation in the stock ledger.
3. Updates the linked Delivery Note item rows with the new `handling_unit` values.

BEAM is a required dependency for full SSCC/handling-unit tracking. The Packing Slip can still be used for rate shopping and label purchase without BEAM.

---
