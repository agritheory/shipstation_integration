<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Fulfillment Sync

<div class="byline">
  AgriTheory 2026-06-24
</div>


Fulfillment sync pushes tracking information from ERPNext back to marketplaces connected through ShipStation — for example Shopify, Amazon, and other channels configured in your ShipStation account.

## Prerequisites

- **Enable ShipStation API v2** on **Shipstation Settings**
- Valid **ShipStation API Key**
- A **Delivery Note** with a **Tracking Number** and linked carrier information

Fulfillment uses the v2 API only. Order import still flows through v1 when marketplace sync is enabled.

## Creating a fulfillment

From a submitted **Delivery Note** that has been shipped:

1. Ensure the tracking number and carrier are populated (typically after label purchase — see [Labels and Rate Shopping](./labels_and_rates.md)).
2. Use the fulfillment action on the Delivery Note to create a fulfillment record in ShipStation.

The integration sends:

- Shipment number (Delivery Note name)
- Tracking number and carrier code
- Ship date
- Ship-to address (when available)
- Line items (when present)

ShipStation then propagates tracking updates to the connected marketplace.

## Relationship to v1 shipment sync

| Direction | API | Purpose |
|---|---|---|
| ShipStation → ERPNext | v1 | import orders and shipments from marketplaces |
| ERPNext → marketplaces | v2 | push fulfillment/tracking back to channels |

v1 **Pull Shipments** creates ERPNext documents when ShipStation marks an order shipped. v2 fulfillment is the reverse path — telling marketplaces that ERPNext has shipped the order.

## Webhook tracking updates

v2 `track` webhooks (see [Webhooks](./webhooks.md)) can update Delivery Note tracking status when carriers report changes. This complements manual fulfillment creation.

## Configuration

Enable **ShipStation API v2** and enter your API key on **Shipstation Settings**. No separate fulfillment toggle is required.
