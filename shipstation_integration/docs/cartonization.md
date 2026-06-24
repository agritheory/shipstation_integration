<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Cartonization

<div class="byline">
  AgriTheory 2026-06-24
</div>


Cartonization automatically assigns Delivery Note line items to parcels (bins) when you create a **Packing Slip** or **Shipment** from a Delivery Note. It uses the cartonization solver from **Inventory Tools** to fit items into container templates based on physical dimensions.

## Prerequisites

- **Inventory Tools** app installed and enabled
- **BEAM** app installed (required for handling units on submitted Packing Slips)
- **Enable Cartonization** checked on **Shipstation Settings**

For solver concepts and container modeling, see the Inventory Tools [Cartonization](https://github.com/AgriTheory/inventory_tools/blob/main/inventory_tools/docs/cartonization.md) documentation.

## How it works

When cartonization is enabled, the app overrides ERPNext's standard `make_packing_slip` and `make_shipment` whitelisted methods:

1. The standard Delivery Note mapping runs first.
2. The cartonization solver runs against item physical dimensions and available container templates.
3. Items are assigned to parcels with `parcel_number` on Packing Slip Item or Shipment Delivery Note rows.

If cartonization is disabled or Inventory Tools is not installed, the standard ERPNext behavior is used.

## Settings

On **Shipstation Settings** → **Cartonization**:

| Field | Purpose |
|---|---|
| **Enable Cartonization** | master switch |
| **Auto cartonize Packing Slip from Delivery Note** | run solver when creating a Packing Slip |
| **Auto cartonize Shipment from Delivery Note** | run solver when creating a Shipment |
| **Cartonization Mode** | solver mode (e.g. `3D Volumetric`) |
| **Allow Rotation** | allow items to rotate within bins |
| **Solver Timeout Seconds** | maximum solver runtime |
| **Default Container DocTypes JSON** | list of DocTypes to search for container templates (default: `Shipment Parcel Template`) |
| **Create Physical Dimension per Parcel Template** | auto-create **Physical Dimension** records for parcel templates missing dimensions |

## Container templates

Container candidates come from **Shipment Parcel Template** records (and any additional DocTypes listed in the JSON setting). Each template needs physical dimensions — either entered manually or created via the auto-create toggle above.

## Packing Slip behavior

When cartonization is active:

- Overlapping case-number validation is relaxed; parcels are tracked via `parcel_number`.
- All items must be assigned to a parcel before submit.
- On submit, BEAM handling units are registered for each parcel (see [GS1 / SSCC](./gs1_sscc.md)).

The Packing Slip UI exposes whether cartonization is enabled via a boot-time flag.

## Configuration

Enable cartonization on **Shipstation Settings**. Ensure Inventory Tools and BEAM are installed. Configure parcel templates with dimensions before running cartonization on real orders.
