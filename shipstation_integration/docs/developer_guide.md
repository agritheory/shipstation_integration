<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Developer Guide

<div class="byline">
  AgriTheory 2026-06-24
</div>


This page covers extension points for custom Frappe apps that build on ShipStation Integration. For end-user setup, start with [Shipstation Settings](./shipstation_settings.md).

## Hooks

Register hooks in your app's `hooks.py`. ShipStation Integration reads them at runtime via `frappe.get_hooks()`.

### LTL providers

Map a **Freight Carrier Settings** base URL substring to a `BaseLTL` subclass:

```python
# hooks.py
ltl_providers = {
    "mycarrier.com": "my_app.freight.my_carrier_ltl.MyCarrierLTL",
}
```

Entries are matched in definition order; first match wins. Place custom entries before built-in ones to override ShipEngine or WWEX mappings.

Implement required methods on a class extending `shipstation_integration.base_ltl.BaseLTL`:

- `get_ltl_quotes`
- `schedule_ltl_pickup`
- `track_shipment`
- `get_documents`
- (and other methods as needed for your carrier)

See [LTL](./ltl.md) for built-in providers and credential fields.

### Amazon order processing

When a store is flagged as an Amazon marketplace, order import calls:

```python
update_shipstation_amazon_order = [
    "my_app.shipstation.amazon_customize_order",
]
```

Signature: `(store, shipstation_order, sales_order) -> sales_order`

Use this to adjust taxes, fees, line items, or customer mapping for Amazon orders.

Shopify stores use the parallel `update_shipstation_shopify_order` hook with the same signature.

### Order import hooks

| Hook | Signature / return | Purpose |
|---|---|---|
| `update_shipstation_list_order_parameters` | `(parameters) -> parameters` | modify v1 list-orders API query |
| `process_shipstation_order` | `(ss_order, store) -> bool` | return `False` to skip SO creation |
| `process_shipstation_order_items` | `(order_items) -> order_items` | transform line items before append |

### 17Track

| Hook | Purpose |
|---|---|
| `seventeen_track_status_description_providers` | list of callables returning `(stage, sub_status) -> description` maps |
| `seventeen_track_geocode_address` | `(address_dict) -> (lat, lon) or None` — overrides default Nominatim geocoder |

See [Tracking Map](./tracking_map.md) for geocoding details.

## Whitelisted API modules

Key server-side entry points (not a full API reference):

| Module | Purpose |
|---|---|
| `api.orders` | order list, create from webhook |
| `api.shipments` | shipment list, create SI/DN/Shipment |
| `api.tags` | tag sync |
| `api.rates` | v2 rate shopping |
| `api.labels` | v2 label purchase and void |
| `api.fulfillments` | v2 marketplace fulfillment |
| `api.webhook_receiver` | v1 and v2 webhook endpoints |
| `cartonization` | packing slip / shipment cartonization overrides |
| `overrides.sscc` | SSCC generation whitelisted methods |

## DocType overrides

`hooks.py` registers custom classes for:

- **Sales Order** — ShipStation-specific behavior
- **Shipment** — LTL and SSCC integration
- **Packing Slip** — cartonization and BEAM handling units
- **Shipment Parcel Template** — parcel UOM display

## Custom fields

ERPNext DocType custom fields live under `shipstation_integration/shipstation_integration/custom/`. Run `bench migrate` after pulling app updates to sync field definitions.

## Testing

Use `shipstation_integration.tests.setup.before_test` to seed fixtures. See [Example Data](./exampledata.md).

Pytest modules under `shipstation_integration/tests/` cover LTL providers, webhooks, cartonization, and SSCC.

## Configuration

Custom apps add hooks in their own `hooks.py` — no changes to ShipStation Integration settings are required beyond normal [Shipstation Settings](./shipstation_settings.md) setup.
