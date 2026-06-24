<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Webhooks

<div class="byline">
  AgriTheory 2026-06-24
</div>


ShipStation Integration uses two webhook endpoints — one for the legacy v1 API (store-scoped) and one for ShipStation API v2 (environment-scoped). Both must be reachable over HTTPS from the public internet.

## v1 store webhooks

When **Enable Legacy API (v1)** is checked and **Shipstation Settings** is saved, the app registers webhooks with ShipStation for each store in the **Shipstation Stores** table.

**Endpoint:**

```
{your-site-url}/api/method/shipstation_integration.api.webhook_receiver.shipstation_webhook
```

**Event types registered:**

| Event | Action |
|---|---|
| `ORDER_NOTIFY` | enqueue order import for the store (when **Pull Orders** is enabled) |
| `SHIP_NOTIFY` | enqueue shipment import (when **Pull Shipments** and a create flag are enabled) |
| `ITEM_SHIP_NOTIFY` | same as `SHIP_NOTIFY` for item-level ship notifications |

The webhook payload includes a `resource_url`. The receiver fetches the batch of orders or shipments from ShipStation and enqueues background jobs on the `shipstation` queue.

### v1 prerequisites

- **Shipstation Settings** — **Enabled** and **Enable Legacy API (v1)**
- Store-level **Pull Orders** or **Pull Shipments** as appropriate
- For shipment events: at least one of **Create Sales Invoice**, **Create Delivery Note**, or **Create Shipment** enabled on the store

Webhooks are re-registered on save if they do not already exist for the store and endpoint URL.

## v2 environment webhooks

When **Enable ShipStation API v2** is checked and a valid **ShipStation API Key** is saved, the app registers environment-level webhooks with the ShipStation v2 API.

**Endpoint:**

```
{your-site-url}/api/method/shipstation_integration.api.webhook_receiver.shipstation_api_webhook
```

**Events registered:** `batch`, `track`

| Event | Handler |
|---|---|
| `batch` | batch label processing completion |
| `track` | tracking status updates on Delivery Notes |
| `carrier_connected` | logged when received (no automatic action) |
| `label_created` | logged when received |
| `sales_order_status_change` | logged when received |

v2 webhooks are only created if the endpoint URL is not already registered in your ShipStation environment.

## 17Track webhooks

17Track uses a separate endpoint on the **Seventeen Track** DocType. See [17Track Integration](./17track.md).

## Troubleshooting

1. **HTTPS required** — ShipStation and 17Track require a publicly reachable HTTPS URL. Local development sites need a tunnel (ngrok, etc.).
2. **Check Error Log** — webhook handler failures are logged in ERPNext's Error Log.
3. **Debug logging** — v2 webhooks write to the `shipstation` logger at debug level.
4. **Re-save settings** — saving **Shipstation Settings** re-runs webhook registration for missing v1/v2 subscriptions.
5. **Store not found** — v1 webhooks include a `storeID` query parameter that must match a **Shipstation Store** row.
6. **Since Date filter** — shipments before **Since Date** on **Shipstation Settings** are skipped.

## Configuration

Webhook registration is automatic when you save an enabled **Shipstation Settings** document with the appropriate API version enabled. No manual URL entry is required for ShipStation v1/v2 webhooks.
