<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# ShipStation Integration Documentation

<div class="byline">
  AgriTheory 2026-06-24
</div>


The ShipStation Integration app connects ERPNext to [ShipStation](https://www.shipstation.com/) for order sync, shipping labels, rate shopping, LTL freight, and global parcel tracking. It supports the legacy ShipStation API (v1), ShipStation API v2, direct LTL carrier integrations, and 17Track webhooks.

### Core ShipStation

- **[Shipstation Settings](./shipstation_settings.md)**: credentials, v1/v2 API toggles, carrier sync, and global feature switches
- **[Shipstation Stores](./shipstation_stores.md)**: map marketplace stores to companies, warehouses, and accounting defaults
- **[Webhooks](./webhooks.md)**: v1 store webhooks and v2 environment webhooks for real-time order and shipment events
- **[Order Sync](./order_sync.md)**: scheduled and webhook-driven import of orders, items, tags, and shipments
- **[Labels and Rate Shopping](./labels_and_rates.md)**: compare carrier rates and purchase or void labels from Delivery Notes, Packing Slips, and Shipments
- **[Fulfillment Sync](./fulfillment_sync.md)**: push tracking numbers back to connected marketplaces via ShipStation API v2

### Freight and tracking

- **[Less Than Truckload (LTL)](./ltl.md)**: rate quotes, booking, BOL attachment, and tracking on the Shipment document
- **[17Track Integration](./17track.md)**: webhook-driven global tracking and status updates
- **[Tracking Map](./tracking_map.md)**: live map of shipment events on the Tracking Number form

### Packing and display

- **[Cartonization](./cartonization.md)**: multi-bin packing when creating Packing Slips and Shipments from Delivery Notes (requires Inventory Tools)
- **[GS1 / SSCC](./gs1_sscc.md)**: SSCC-18 code generation on Packing Slips and Shipments
- **[Parcel Measurement UOM Preference](./parcel_dimension_uom_conversion.md)**: store dimensions in metric units while displaying each user's preferred UOM

### Operations and development

- **[Background Jobs](./background_jobs.md)**: the dedicated `shipstation` worker queue and scheduler tasks
- **[Example Data](./exampledata.md)**: install demo data to experiment with the app on a test site
- **[Developer Guide](./developer_guide.md)**: extension hooks for LTL providers, Amazon orders, geocoding, and more

## Configuration

Most features are controlled from **Shipstation Settings**. You may have one settings document per ShipStation account. Store-level behavior (company, warehouse, document creation flags) is configured in the **Shipstation Stores** child table on that document.

| Feature area | Primary configuration |
|---|---|
| Order sync, v1 labels, stores | **Shipstation Settings** — Legacy API (v1) |
| Rate shopping, v2 labels, fulfillments | **Shipstation Settings** — ShipStation API v2 |
| LTL freight | **Freight Carrier Settings** per company + transporter (see [LTL](./ltl.md)) |
| 17Track tracking | **Seventeen Track** per company (see [17Track](./17track.md)) |
| Cartonization | **Shipstation Settings** — Cartonization section (requires Inventory Tools) |
| SSCC codes | **Shipstation Settings** — GS1 Company Prefix |
| Parcel UOM display | **User** — preferred dimension and weight UOM |

![Screenshot of the Shipstation Settings form showing API credentials, store table, and feature toggles](./assets/settings.png)

## Installation

Full [installation instructions](https://github.com/AgriTheory/shipstation_integration) are in the application repository. The app requires **ERPNext**, **BEAM**, and **Inventory Tools**.

To load demo data on a test site, see [Example Data](./exampledata.md).
