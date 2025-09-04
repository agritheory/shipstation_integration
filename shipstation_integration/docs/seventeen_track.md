# 17Track Integration

This integration connects [17TRACK](https://www.17track.net/) global shipment tracking with ERPNext via the Shipstation Integration app.  
It enables automatic tracking, status sync, and comment logging for shipments handled via 17TRACK.

---

## Features

- **Automatic tracking updates via webhook from 17TRACK**
- **Carrier management** for fast lookup and autosuggest
- **Configurable comment logging** on shipment status updates

---

## Setup

### **Seventeen Track Doctype**

- Go to **Seventeen Track** DocType.
- **Fields:**
  - `API Key`: Your 17TRACK API key. _(Required)_
  - `Add Updates as Comments`: Enable to log tracking status changes as comments on Shipment.

--- 

## **Shipment Doctype Custom Fields**

The following custom fields are added to the Shipment doctype:

| Fieldname                      | Label                             | Type     | Description                 |
|--------------------------------|-----------------------------------|----------|-----------------------------|
| seventeen_track_carrier        | Seventeen Track Carrier           | Link     | Linked Carrier              |
| seventeen_track_status         | Seventeen Track Status            | Select   | Latest 17TRACK status       |
| seventeen_track_sub_status     | Seventeen Track Sub Status        | Data     | Latest sub status           |
| seventeen_track_description    | Seventeen Track Description       | Data     | Latest event description    |
| seventeen_track_latest_status_time | Seventeen Track Latest Status Time | Data     | Event time (UTC)            |

---

## Carriers

- Carrier information is fetched from 17TRACK's API to a Virtual Doctype and cached for fast lookup.
- **Manual Refresh:** On the **Carrier list view**, click the **"Update Carriers"** button to refresh the cache.

---

## Automated Shipment Tracking Actions

This integration **overrides ERPNext’s Shipment class** with a custom class to automate registration, retracking, and cancellation actions with 17TRACK:

**Behavior:**
- **On Submit:** 
  - If `shipment_id` is set, the tracking number will be registered. The `seventeen_track_carrier` will be used if provided—this is optional but recommended for accurate Carrier identification.
  - If the Shipment is amended and the original’s `shipment_id` matches, calls retrack on 17TRACK.
- **On Cancel:**  
  - Stop the tracking on 17TRACK for the Shipment.

---

## Webhook Integration

### **Endpoint**

`/api/method/shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track.seventeentrack_webhook`

#### **How it works:**

- Receives POST requests from 17TRACK on shipment status updates.
- Updates corresponding Shipment record:
  - Sets latest status, sub-status, description, event time, and carrier.
  - Optionally adds a comment if enabled in Seventeen Track settings.

---

## Example: Comment Logged on Shipment

If `Add Updates as Comments` is enabled, the following is added to Shipment as a comment on status update:

```
**17TRACK Update**

- **Status:** Delivered
- **Sub-status:** Delivered_Other
- **Sub-status description:** 
- **Event Time:** 2025-09-04 08:49:55.826975
- **Description:** Package delivered to recipient.
```


---

## API Key

- Obtain your 17TRACK API key from [17TRACK](https://www.17track.net/) account.
- Enter it in Seventeen Track DocType.
