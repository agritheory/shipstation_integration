<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Shipstation Stores

<div class="byline">
  AgriTheory 2026-06-24
</div>


Each row in the **Shipstation Stores** child table on **Shipstation Settings** represents one marketplace store connected to your ShipStation account. Store configuration determines which ERPNext company and warehouse receive imported orders, and which downstream documents are created automatically.

Stores are populated when you run **Update Carriers and Stores** on **Shipstation Settings** (requires Legacy API v1). **Store ID**, **Store Name**, and **Marketplace Name** are read-only and come from ShipStation.

## Company and stock defaults

| Field | Purpose |
|---|---|
| **Company** | ERPNext company for Sales Orders and related documents |
| **Fulfillment Warehouse** | default warehouse on Sales Order line items |
| **Currency** | transaction currency (defaults from company) |
| **Customer** | optional default customer link |

## Account defaults

Map shipping revenue, tax, and commission accounts for imported orders:

- **Cost Center**
- **Shipping Income Account** / **Shipping Expense Account**
- **Tax Account**, **Sales Account**, **Expense Account**
- **Difference Account** (required) — absorbs rounding or coupon differences between ShipStation and ERPNext totals
- **Commission Account** and **Apply Commission** — when a **Sales Partner** is set
- **Withholding** — merchant remits sales tax directly to the tax authority

## Sync toggles

| Field | Purpose |
|---|---|
| **Pull Orders** (`enable_orders`) | include this store in scheduled and webhook order import |
| **Pull Shipments** (`enable_shipments`) | include this store in shipment import (requires Pull Orders) |

When **Pull Shipments** is enabled, you can also enable automatic document creation:

| Field | Purpose |
|---|---|
| **Create Sales Invoice** | create a submitted Sales Invoice when a ShipStation shipment is imported |
| **Create Delivery Note** | create a Delivery Note against the Sales Order |
| **Create Shipment** | create an ERPNext Shipment from the Delivery Note |

At least one of the three create flags must be enabled for shipment webhooks and the shipment scheduler to process this store.

If **Create Delivery Note** is disabled, the integration attempts to match existing submitted Delivery Notes by ShipStation order ID when creating Shipments.

## Amazon stores

When ShipStation reports a store as an Amazon marketplace, **Is Amazon Store** and **Amazon Marketplace** are set automatically (read-only).

For Amazon-specific order logic (FBA fees, marketplace tax, custom line items), register the `update_shipstation_amazon_order` hook in a custom app. See the [Developer Guide](./developer_guide.md).

Shopify stores can use the similar `update_shipstation_shopify_order` hook.

## Shipstation Warehouse mapping

ShipStation warehouse IDs are mapped separately in the **Shipstation Warehouses** child table on **Shipstation Settings**. Link each ShipStation warehouse to an ERPNext **Warehouse** so fulfillment locations resolve correctly during order import.

## Configuration

Open **Shipstation Settings**, expand the **Shipstation Stores** section, and configure each store row. Global API credentials and sync buttons are on the parent **Shipstation Settings** document — see [Shipstation Settings](./shipstation_settings.md).
