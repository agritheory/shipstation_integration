<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Tracking Map – Feature Overview

<div class="byline">
  Lautaro 2026-05-14
</div>

## Purpose

This feature displays a **live map on the Tracking Number form** that plots the physical journey of a shipment based on carrier events received via 17Track webhooks.
Each event with a known location is shown as a pin on the map, with the most recent one highlighted in red.

---

## Where this applies

* **Tracking Number** (form view — Tracking Map section)
* **Seventeen Track** settings (per company)

---

## How events are collected

Every time 17Track sends a `TRACKING_UPDATED` webhook, the integration parses the events from each provider and appends new rows to the **Tracking Number Event** child table.
Events are deduplicated by UTC timestamp (to second precision), so re-delivered webhooks never create duplicate rows.

Each row stores:

* Event time, stage, and description
* Full address breakdown (country, state, city, street, postal code)
* Raw location string as reported by the carrier
* Latitude and longitude (if resolved)
* Coordinates source: `API`, `Geocoded`, or empty
* Provider name (e.g. UPS, FedEx)

---

## Coordinate resolution

Not all carriers include coordinates in their event data.
When coordinates are missing, the integration can call a geocoding service to resolve them from the address.

The resolution order is:

1. **API coordinates** — if the carrier included `latitude`/`longitude` in the event, those are used directly (`coordinates_source = API`).
2. **Geocoding hook** — if coordinates are absent and **Enable Geocoding** is checked on the Seventeen Track settings doc, the `seventeen_track_geocode_address` hook is called. The default implementation uses **Nominatim (OpenStreetMap)**. If it returns coordinates, they are stored with `coordinates_source = Geocoded`.
3. **No coordinates** — if geocoding is disabled or the hook returns nothing, the event is still recorded in the child table but it will not appear as a pin on the map.

```
Webhook event received
        │
        ▼
Does the API include lat/lon?
        │
       Yes ──────────────────────────► source = "API"
        │
        No
        │
        ▼
Is "Enable Geocoding" checked?
        │
       No ───────────────────────────► source = "" (no map pin)
        │
       Yes
        │
        ▼
Is a seventeen_track_geocode_address hook registered?
        │
       Yes ──────────────────────────► Call hook (default: Nominatim)
        │                                    │
        │                             Did it return coords?
        │                                  Yes ──► source = "Geocoded"
        │                                   No ──► source = ""
        No ──────────────────────────► source = "" (no map pin)
```

---

## Enable Geocoding flag

The **Enable Geocoding** checkbox on the **Seventeen Track** settings doc (default: on) controls whether geocoding calls are made when processing webhooks.

Disable it if:

* You have not configured a geocoding provider and do not want external HTTP calls.
* You are in an environment where outbound requests to geocoding APIs are not permitted.

Events will still be recorded in the child table; only the coordinate fields will remain empty.

---

## Replacing the default geocoder

The default geocoder (Nominatim) is suitable for development and low-volume production.
For high-volume use, register your own implementation by adding a `seventeen_track_geocode_address` hook in your custom app's `hooks.py`:

```python
# your_app/hooks.py
seventeen_track_geocode_address = [
    "your_app.geocoding.your_geocode_function",
]
```

The function receives an address dict with keys `street`, `city`, `state`, `postal_code`, `country`, and `location`, and must return `(latitude, longitude)` or `None`.

```python
# your_app/geocoding.py
def your_geocode_function(address: dict) -> tuple[float, float] | None:
    # call your preferred geocoding API
    ...
```

The last registered hook in the list takes precedence, so your app's hook will override the default Nominatim implementation.

---

## Map rendering

The map is rendered in the **Tracking Map** section of the Tracking Number form using **Leaflet.js**, which is already bundled with Frappe — no additional dependencies are required.

Only events with `coordinates_source = API` or `coordinates_source = Geocoded` are plotted.
Events without coordinates are visible in the **Tracking Number Event** child table but are not shown on the map.

### Stage filter

A dropdown above the map allows filtering by stage (e.g. OutForDelivery, Delivered).
Selecting a stage shows only the pins for that leg of the journey.
The red pin always marks the **globally most recent event**, regardless of which stage is selected.

### Marker colors

| Marker | Meaning |
|--------|---------|
| Blue (default) | Past event |
| Red pin | Most recent event overall |

Clicking any pin opens a popup with the stage, description, location, and timestamp of that event.

---

## Tracking Number Event child table

The child table is read-only and populated exclusively by the webhook handler.
It is visible on the Tracking Number form below the References section.

| Field | Type | Description |
|-------|------|-------------|
| Event Time | Datetime | UTC timestamp of the event |
| Stage | Select | 17Track key stage (InfoReceived, Delivered, …) |
| Description | Small Text | Carrier-provided event description |
| Location | Data | Raw location string from the carrier |
| Country / State / City / Street / Postal Code | Data | Parsed address fields |
| Latitude / Longitude | Float | Resolved coordinates (empty if not available) |
| Coordinates Source | Select | API, Geocoded, or empty |
| Provider | Data | Carrier or data provider name |

---
