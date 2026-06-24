<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Labels and Rate Shopping

<div class="byline">
  AgriTheory 2026-06-24
</div>


ShipStation Integration supports two label workflows: v1 label generation through ShipStation's order-based workflow, and v2 direct label purchase with optional rate shopping via ShipStation API v2 (ShipEngine).

## Prerequisites

| Workflow | Requires |
|---|---|
| v1 labels | **Enable Legacy API (v1)** and **Enable Label Generation** on **Shipstation Settings** |
| v2 rate shopping / labels | **Enable ShipStation API v2** and a valid **ShipStation API Key** |
| Carrier metadata (v2) | run **Fetch API Carriers** and **Sync Carrier Packages** on settings |

## Rate shopping (v2)

Rate shopping compares shipping costs across connected carriers before you purchase a label.

Available from:

- **Delivery Note** — rate shopping UI in the form
- **Packing Slip** — when v2 is enabled
- **Shipment** — for small-parcel shipments

The integration builds a shipment payload from the document's ship-from/ship-to addresses and parcel dimensions, then calls the ShipEngine rate API. Results show carrier, service, and price so you can select the best option.

Parcel dimensions and weights are converted to ShipEngine-compatible units (inch/centimeter, pound/kilogram). See [Parcel Measurement UOM Preference](./parcel_dimension_uom_conversion.md) for how stored metric values are displayed.

## Label purchase (v2)

After selecting a rate (or providing carrier/service codes directly), **Create Label** purchases the label through the v2 API.

- Tracking number and label download URL are written back to the source document.
- Label PDF/ZPL can be saved as a **File** attachment.
- If the carrier rejects third-party billing, the integration retries without it and notifies the user.

**Void Label** cancels a purchased v2 label when supported by the carrier.

Whitelisted methods live in `shipstation_integration.api.labels` and `shipstation_integration.api.rates`.

## Label generation (v1)

When **Enable Label Generation** is checked, Delivery Notes linked to a ShipStation order can generate labels through the legacy v1 API. This uses carrier, service, and package metadata from the v1 **Carrier Data** section on **Shipstation Settings**.

Use v1 when orders originate in ShipStation marketplaces and you want labels created through ShipStation's native order workflow.

Use v2 when you need to ship without a ShipStation order — for example, ad-hoc shipments from ERPNext Delivery Notes.

## Choosing v1 vs v2

| Scenario | Recommended API |
|---|---|
| Orders synced from Amazon/Shopify via ShipStation | v1 for labels tied to ShipStation orders |
| Rate comparison before purchase | v2 |
| Direct label purchase without ShipStation order | v2 |
| Both marketplace sync and rate shopping | enable both — see [Shipstation Settings](./shipstation_settings.md) |

## Configuration

Enable the appropriate API sections and toggles on **Shipstation Settings**. Sync v2 carriers before rate shopping. For v1, refresh carrier data via **Update Carriers and Stores**.
