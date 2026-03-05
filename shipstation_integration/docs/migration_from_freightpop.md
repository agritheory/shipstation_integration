<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# v15 Shipping Migration Guide

This document describes the shipping-related changes made during the v15 migration: what was removed, what data was moved, and what the current system looks like.

---

## Background

The previous shipping stack lived in `upro_erp_integ` and was built around **FreightPOP**, a third-party logistics aggregator. It used a custom REST client (`freight_pop.py`) with username/password token auth, per-carrier ID mappings stored in proprietary Doctypes, and separate quote/ship flows triggered from a button on the Packing Slip.

That entire integration has been removed. `upro_erp_integ` itself is no longer installed. Shipping is now handled by the **`shipstation_integration`** app, which uses the ShipEngine/ShipStation API v2 and a standard Frappe app structure.

The legacy code is preserved in the `upro_erp_integ` git history if reference is needed.

---

## What Was Removed

### In `upro_erp_integ`

The app contained:

- **FreightPOP API client** — `freight_pop.py` with `authenticate()`, `get_rates()`, `ship_with_quote()`, `ship_without_quote()`, `cancel_shipment()`, `update_freight_charges()`, `get_carrier_list()`


**Doctypes removed with the app:**

| Doctype | What it held |
|---|---|
| `Freight Pop Settings` | API credentials, base URL, endpoint config |
| `Freight Carrier List` | Mapped FreightPOP carrier IDs to internal sales channels |
| `Freight Account Settings` | Mapped sales channels to GL accounts for freight charge postings |
| `Freight Sales Channel Mapping` | Mapped `up_sales_channel` values to FreightPOP channel names |
| `Shipping Carrier` | Carrier name, ID, service per sales channel — populated by daily cron |
| `Alternate Shipping Rule Name` | Child table on `Shipping Carrier` for Shopify carrier name variants |
| `Packing Slip Shipment` | Child table on Packing Slip: `tracking_number`, `tracking_url`, `shipping_label`, `bol` |

**Custom fields removed from Delivery Note:**

| Field | Purpose |
|---|---|
| `up_shipping_info` | Child table linking to `Packing Slip Shipment` |
| `up_freight_cost` | Actual freight cost written back from FreightPOP |
| `up_is_free_shipping` | Flag to skip freight charge GL posting |
| `shipment_tracking_success` | FreightPOP ShipmentId on successful booking |
| `shipment_tracking_failure` | Raw error payload on failed booking |
| `shipment_quotation_success` | Raw rate quote response JSON |
| `shipment_quotation_failure` | Raw error payload on failed rate quote |

### `upro_erp` Shipping Doctypes

Two imperial/metric dimension child tables used to pass box sizes to FreightPOP were removed:

- `Package Dimensions Detail` — imperial (`length_inch`, `breadth_inch`, `height_inch`, `gross_weight_kilograms`)
- `Package Dimensions Detail European` — metric (`length_cm`, `breadth_cm`, `height_cm`)

The `Shipping Rule` class override in `upro_erp` (`UPROShippingRule`) was also removed — it was tied to the FreightPOP carrier mapping system.

### Legacy `up_*` Custom Fields on Packing Slip

A large set of `up_*` Custom Fields existed on Packing Slip — added via Customize Form, not exported to any app fixture. These covered:

- Consignee and shipper address components (city, state, zip, address lines, phone, email)
- Freight selection and carrier options
- Shipment workflow UI fields (buttons, status fields, response blobs)
- Package dimension child table links and weight fields
- Tracking child table link

All were removed after data migration. See [Packing Slip Migration](#packing-slip-migration) below for details on what data was moved first.

**Fields intentionally kept** (still in active use, no equivalent in `shipstation_integration`):
- `up_sales_channel`
- `up_other_inputs`

### Legacy `up_*` Barcode Fields on Item

Four custom fields stored barcode data as SVG renders rather than plain values. These were migrated to the standard ERPNext `Item Barcode` child table and then removed. See [Item Barcode Migration](#item-barcode-migration) below.

---

## Scope: Small Parcel Only

ShipStation handles **small parcel** shipments only. LTL, full truckload, and customer pickup freight are outside its scope and must be managed through other means (manual booking, a separate TMS, etc.).

The `freight_type` field on Packing Slip reflects this — its dropdown is restricted to `Small Parcel`. Records migrated from the legacy system that had `LTL`, `Full Truckload`, or `Customer Pickup` as their `freight_type` will retain that value in the database, but the field will display it as a freetext fallback. Those Packing Slips should not be processed through ShipStation and will need to be managed outside the integration.

---

## What `shipstation_integration` Provides

### Configuration

`Shipstation Settings` is the single configuration doctype. It holds the ShipStation API key, store mappings, warehouse mappings, and a JSON cache of carrier/package type data synced from ShipStation.

### Carrier Management

Carriers are **Supplier records** with `is_transporter = 1`. They are created and kept in sync via `carriers.sync_carrier_package_types()`, callable on demand or via the Settings UI. Carrier data (IDs, codes, services, package types) is cached as JSON in `Shipstation Settings.shipstation_api_carrier_data`.

Custom fields added to `Supplier`:
- `preferred_carrier` — Link to another Supplier (the preferred transporter for this vendor)
- `shipping_accounts` — Child table (`Shipping Account`) for third-party billing account numbers per carrier
- `carrier_codes` — Tab Break grouping the above fields

### Rate Shopping

`rates.get_rates()` calls the ShipEngine API and returns a multi-carrier rate comparison. Package dimensions are provided via the `Parcel Dimensions` child table on Packing Slip (see below). UOM conversion (inch ↔ cm, lb ↔ kg) is handled in `rates.py` — no manual `pack_type` toggle required.

### Label Purchase and Cancellation

`labels.purchase_label()` purchases a shipping label via ShipEngine. The label PDF is attached to the Delivery Note as a Frappe File. `labels.void_label()` cancels a booked label.

### Fulfillment Writeback

`fulfillments.create_fulfillment()` pushes tracking and fulfillment data to ShipStation, which distributes it to all connected marketplaces (Shopify, Amazon, etc.). This replaces the per-marketplace Shopify scripts that previously lived in `upro_erp_integ`.

### Order and Shipment Sync

`orders.queue_orders()` polls ShipStation for new orders across all connected stores. `shipments.queue_shipments()` polls for shipment status updates. Both run via the `all` scheduler hook with queue-based deduplication, replacing the every-10-minute cron jobs in `upro_erp_integ`.

### Delivery Note Fields Added

| Field | Purpose |
|---|---|
| `shipstation_shipment_id` | ShipStation shipment ID after label purchase |
| `marketplace` | Which sales channel / marketplace the order came from |
| `marketplace_order_id` | External order ID from the marketplace |
| `shipstation_store_name` | Which ShipStation store the order belongs to |
| `fulfilled_by` | Which party fulfilled the order |
| `shipstation_customer_notes` | Customer-facing notes synced from ShipStation |
| `shipstation_internal_notes` | Internal notes synced from ShipStation |

Tracking is stored on the standard ERPNext `tracking_number` field on Delivery Note; the label is attached as a File rather than stored in a custom field.

---

## Data Migrations

### Packing Slip Migration

**Patch:** `upro_erp.patches.v15.migrate_packing_slip_to_shipstation`

This patch runs automatically via `patches.txt`. It performs all Packing Slip data migration in a single pass:

**1. Package dimensions → Parcel Dimensions**

Rows from `Package Dimensions Detail` (imperial) and `Package Dimensions Detail European` (metric) are migrated to the `Parcel Dimensions` child table on Packing Slip, with UOM fields set appropriately (`Inch`/`Pound` or `Centimeter`/`Kilogram`). Only Packing Slips that don't already have a `Parcel Dimensions` row are processed.

**2. Tracking data → Parcel Dimensions**

Each row in `Packing Slip Shipment` with a tracking number is mapped to a `Parcel Dimensions` row. If a row already exists (from step 1), it is updated with tracking data. If the Packing Slip had multiple tracking numbers (multi-tracking), additional stub rows are created — one per tracking number. `tracking_url`, `label_url`, and `bol_url` are extracted from HTML anchor tags if the legacy data stored them as `<a href="...">` (FreightPOP's format).

**3. Freight type and carrier service**

`up_freight_selection` → `freight_type` (direct copy, same Select options).

`up_shipment_carrier_options` or `up_sh_carrier_options` (first non-empty) → `carrier_service`.

**4. Shipping and dispatch addresses**

`shipping_address_name` and `dispatch_address_name` are populated from the linked Delivery Note's address fields. Before writing, each address is validated against the old denormalized `up_consignee_*` / `up_shipper_*` fields:

- If the old fields are empty, migration proceeds (nothing to contradict).
- If city, state, or pincode disagree, the record is **skipped** and logged to Error Log under `"Packing Slip Shipping Address Mismatch"` for manual review.
- `address_line1` uses a looser word-overlap check (legacy data often stored contact names in line 1).
- If the Address record itself no longer exists in the database, the record is skipped and logged.

**5. Custom field and column cleanup**

After data migration, all legacy shipping `up_*` Custom Fields are deleted and any orphaned `up_*` columns remaining in `tabPacking Slip` are dropped. `up_sales_channel` and `up_other_inputs` are preserved.

**Post-migration cleanup (manual)**

After verifying the migration, delete the legacy child DocTypes and their backing tables:

```bash
bench --site <site> console
```
```python
import importlib
from upro_erp.patches.v15 import migrate_packing_slip_to_shipstation as p
importlib.reload(p)
p.remove_customizations(dry_run=True)   # preview
p.remove_customizations(dry_run=False)  # execute
```

This removes `Package Dimensions Detail`, `Package Dimensions Detail European`, and `Packing Slip Shipment`.

---

### Item Barcode Migration

**Patch:** `upro_erp.patches.v15.migrate_item_barcodes`

Legacy barcode data on **Item** was stored in four custom fields as SVG renders rather than plain barcode strings. These are migrated to the standard ERPNext `Item Barcode` child table (`Item.barcodes`), which `beam` uses for label printing.

**Field mapping:**

| Legacy field | Barcode type | Notes |
|---|---|---|
| `up_product_upc` | UPC-A | Plain text field, zero-padded to 12 digits |
| `up_product_upc_bar_code` | UPC-A or UPC-E | SVG field; 8-digit → UPC-E, otherwise UPC-A |
| `up_display_upc_bar_code` | UPC-A | SVG field |
| `up_scc_14_bar_code` | ITF-14 | SVG field, zero-padded to 14 digits |

The raw barcode value is extracted from SVG via the `data-barcode-value="..."` attribute, falling back to the first `<text>` element. `barcode_type` options are widened before the migration runs to ensure `beam`'s restricted option set doesn't block inserts.

**Rules:** disabled items are skipped; global duplicates (same barcode on another Item) are logged and skipped; the patch is idempotent.

**Forensic scan** (read-only audit of unmigrated barcodes):

```bash
bench --site <site> execute upro_erp.patches.v15.migrate_item_barcodes.forensic
```

Writes a CSV to `private/files/unmigrated_barcodes_<timestamp>.csv` with one row per unmigrated value, classified by reason: `item_disabled`, `parse_failed`, `normalize_failed`, `global_duplicate_on_<item>`, `not_migrated`.

**Custom field cleanup** (manual, run after verifying migration):

```bash
bench --site <site> migrate  # remove_item_barcode_custom_fields is commented out in patches.txt
```

Uncomment `upro_erp.patches.v15.remove_item_barcode_custom_fields` in `patches.txt` and run `bench migrate`. The patch includes a safety guard — it aborts if fewer than 100 `Item Barcode` records exist.

Fields and columns removed: `up_product_upc`, `up_product_upc_bar_code`, `up_display_upc_bar_code`, `up_scc_14_bar_code`.
