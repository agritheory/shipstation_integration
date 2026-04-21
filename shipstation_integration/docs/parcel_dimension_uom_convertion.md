<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Parcel Measurement UOM Preference – Feature Overview

<div class="byline">
  Ishwarya and Tyler Matteson 2026-02-23
</div>

## Purpose

This feature allows parcel dimensions and weight to be **stored in metric units** while displaying values to each user in their **preferred unit of measurement (UOM)**.
It ensures consistent data storage across the system while providing a context-aware display experience.

---

## Where this applies

* **Shipment Parcel Template**
* **Parcel Dimensions** (child table inside Packing Slip)

---

## Key Concept

All parcel measurements are stored in a **standard internal format**:

* Length → Centimeter
* Width → Centimeter
* Height → Centimeter
* Weight → Kilogram

However, users may prefer to view values in different units (for example inches or pounds).
This feature converts stored metric values into the user’s preferred units for display.

---

## User Preference

Each user can select their preferred units:

* Preferred Dimension UOM
* Preferred Weight UOM

These are stored on the **User** record and loaded during login.
If a user has not selected anything, the system defaults to metric:

* Length → Centimeter
* Weight → Kilogram

---

## How It Works

### 1. Storage

All parcel data is saved in metric units regardless of user preference.
This ensures consistency for integrations, calculations, and APIs.

### 2. Display Conversion

When a document is viewed:

1. The system checks the logged-in user’s preferred units.
2. It retrieves the appropriate conversion factor.
3. Stored metric values are converted to the user’s units.
4. Converted values are shown in read-only display fields.

This happens for:

* Shipment Parcel Template records
* Parcel rows inside Packing Slip (child table)

### 3. Context-Aware Labels

Field labels may change depending on the user’s chosen unit.
For example:

* Length (cm) → Length (in)
* Weight (kg) → Weight (lb)

This ensures clarity for users working in different measurement systems.

---

## Behavior in Child Tables

For **Parcel Dimensions** inside Packing Slip:

* Metric values are stored in each row.
* Display values are calculated based on the current user.
* Conversion happens when the record is saved.
* The stored data remains unchanged.

---

