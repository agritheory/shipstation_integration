<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# GS1 / SSCC

<div class="byline">
  AgriTheory 2026-06-24
</div>


The app generates **SSCC-18** (Serial Shipping Container Code) barcodes for parcels on **Packing Slips** and **Shipments**. SSCC codes identify logistics units and integrate with BEAM **Handling Units** for warehouse tracking.

## Prerequisites

- **BEAM** app installed (Handling Unit inventory dimension)
- **GS1 Company Prefix** configured on **Shipstation Settings**
- Items assigned to parcels (`parcel_number`) on the Packing Slip or Shipment

## Configuration

On **Shipstation Settings** → **GS1 / SSCC**:

- **GS1 Company Prefix** — numeric prefix issued by GS1 (7–10 digits). Required for code generation.

The prefix combines with a per-company serial sequence (`{company-abbr}-SSCC`) and a GS1 check digit to produce valid 18-digit SSCC codes.

## Generating codes

### Packing Slip

Use the **Generate SSCC** action on a Packing Slip. The integration:

1. Groups **Packing Slip Item** rows by `parcel_number`.
2. Skips parcels that already have a `ucc128` value.
3. Generates one SSCC per remaining parcel and writes it to `ucc128` on each row in that parcel.

SSCC generation is a deliberate manual step before submit. Parcels without an SSCC receive a regular BEAM Handling Unit (UUID-derived name) on submit.

### Shipment

Use the **Generate SSCC** action on a Shipment. The same grouping logic applies to **Shipment Delivery Note** child rows.

Codes can also be assigned automatically during Shipment submit hooks when configured.

## BEAM integration

When a Packing Slip with parcel assignments is submitted:

1. A BEAM **Handling Unit** is registered for each SSCC (or UUID fallback).
2. A Repack **Stock Entry** records the handling-unit transformation.
3. **Delivery Note** item `handling_unit` fields are updated to the new SSCCs.

See BEAM documentation for handling unit inventory dimension setup.

## Print formats

The app includes print formats for SSCC labels:

| Print Format | Purpose |
|---|---|
| **SSCC 18 Label** | standard SSCC label for Packing Slip parcels |
| **Shipment SSCC Label** | SSCC label for Shipment parcels |
| **ZPL Preview** | preview ZPL label output |
| **Shipstation Integration BOL** | Bill of Lading (also used for LTL — see [LTL](./ltl.md)) |

Print an SSCC label from the Packing Slip or Shipment form after codes are generated.

## Configuration

Set **GS1 Company Prefix** on **Shipstation Settings**. Ensure BEAM is installed and migrated before generating SSCC codes in production.
