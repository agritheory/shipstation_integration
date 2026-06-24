<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Background Jobs

<div class="byline">
  AgriTheory 2026-06-24
</div>


ShipStation Integration uses a dedicated Frappe background queue and scheduled jobs to import orders, shipments, and tags without blocking the web request cycle.

## Custom queue

On install (`after_install`), the app adds a `shipstation` worker queue to `common_site_config.json`:

```json
{
  "workers": {
    "shipstation": {
      "timeout": 8000
    }
  }
}
```

On production benches with supervisor or systemd restart enabled, the installer prompts to run `bench setup supervisor` so the new worker is picked up.

### Running the worker

Development:

```bash
bench worker --queue shipstation
```

Production (supervisor): ensure a worker process is configured for the `shipstation` queue alongside default, short, and long workers.

If the queue is missing, re-run migrate (the `add_custom_queue` patch is idempotent) or execute:

```python
from shipstation_integration.install import add_custom_queue
add_custom_queue()
```

## Scheduled jobs

Registered in `hooks.py` under `scheduler_events` → `all`:

| Enqueued method | Purpose |
|---|---|
| `shipstation_integration.api.tags.queue_tags` | sync ShipStation tags |
| `shipstation_integration.api.orders.queue_orders` | import orders from all enabled stores |
| `shipstation_integration.api.shipments.queue_shipments` | import shipments and create downstream documents |

Each `queue_*` function checks `is_job_queued` before enqueuing to avoid duplicate runs.

## Webhook jobs

v1 and v2 webhooks enqueue work on the `shipstation` queue rather than processing synchronously:

- Order creation from `ORDER_NOTIFY`
- Shipment creation from `SHIP_NOTIFY` / `ITEM_SHIP_NOTIFY`

See [Webhooks](./webhooks.md) and [Order Sync](./order_sync.md).

## Timeouts

The `shipstation` queue uses an 8000-second job timeout to accommodate large order batches from ShipStation API calls (order list requests use a 5-minute HTTP timeout).

## Configuration

No per-site toggle — the queue is created at install. Ensure at least one worker listens on `shipstation` in every environment that runs order sync or webhooks.
