<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Less Than Truckload (LTL)

<div class="byline">
  Heather Kusmierz and Tyler Matteson 2026-05-26
</div>


Shipstation Integration app integrates less-than-truckload (LTL) functionality into ERPNext's Shipment document. Rates, booking, tracking, and document retrieval can be performed either through ShipStation's ShipEngine API or directly against a carrier's own API.

For v2 API setup and automatic Freight Carrier Settings sync, see [Shipstation Settings](./shipstation_settings.md).

## Configuration

### LTL always uses Freight Carrier Settings

Every LTL HTTP call resolves credentials and the base URL from **Freight Carrier Settings** for the shipment's **Company** + **Preferred Carrier** (transporter supplier). The Shipstation Settings API key is **not** read for LTL requests.

- One **Freight Carrier Settings** row per **Company** + **Supplier** (transporter).
- **Base URL** — determines which provider class handles the shipment (see Provider Selection below). Defaults to `https://api.shipengine.com` when blank.
- **Client ID** / **Client Secret** — meaning depends on provider: **Banyan** OAuth client credentials; **ODFL** API username/password; **TrafficTech** TT Interactive portal email and password (embedded in the rate request body); not used for **WWEX** (Connected App) or **ShipEngine** (LTL API Key).
- **LTL API Key** — **ShipEngine** `Api-Key` header. **Banyan** only: optional static Bearer token if the carrier issues one; if this field is set, it is used **instead of** Client ID/Secret OAuth (do not fill both unless you intend to override with the static token). **TrafficTech** `subscription-key` header on every request.
- **Connected App** — **WWEX** OAuth 2.0 client-credentials (required for WWEX in the current implementation). Optional for **Banyan** if you prefer Centralized OAuth config over FCS Client ID/Secret.

On the Shipment, set **Preferred Carrier** and ensure a **Company** is resolvable (set directly on pickup/delivery when party type is Company, or rely on the user default company).

### ShipStation API v2 and automatic sync

If you enable **ShipStation API v2** on **Shipstation Settings** and save an API key, the system **creates or updates Freight Carrier Settings** for every **Company** × **is_transporter** **Supplier** pair: it copies the API key into **LTL API Key** and sets **Base URL** on each row when that field is blank. Disabled Freight Carrier Settings rows are not updated.

To run **Fetch LTL Carriers** from Shipstation Settings, select **Freight Carrier Settings for LTL fetch** — that record defines which company/supplier context (and thus which synced key) is used for the list call.

You can run LTL **without** enabling ShipStation order sync: create Freight Carrier Settings manually and paste the appropriate credentials.

Legacy **Alternative LTL Client ID/Secret** on Shipstation Settings remain deprecated (hidden); use Freight Carrier Settings instead.

### Provider Selection

The app resolves which provider class to use by matching the **Base URL** in Freight Carrier Settings against the `ltl_providers` hook defined in `hooks.py`. The first matching entry wins. The built-in mapping is:

| Base URL contains | Provider class |
|---|---|
| `shipengine.com` | `ShipstationLTL` (ShipEngine) |
| `shipstation.com` | `ShipstationLTL` (ShipEngine) |
| `wwex.com` | `WwexLTL` |
| `banyantechnology.com` | `BanyanLTL` |
| `traffictech.com` | `TrafficTechLTL` |
| `odfl.com` | `OdflLTL` |

When no Freight Carrier Settings record exists for the shipment, or no entry matches, `ShipstationLTL` is used as the fallback.

## LTL Shipment Workflow

LTL starts from ERPNext's Shipment document with **Freight Type** set to `LTL` and a **Preferred Carrier** selected. Pack the shipment (assign SDN lines to parcels), **submit** the Shipment, then click **Get LTL Quotes** to request contracted rates from the carrier. If the carrier supports spot quotes a checkbox appears to request them instead.

The app stores each returned rate as a **Shipment Quotation** document and displays a summary to the dispatcher. The dispatcher opens the chosen Shipment Quotation and checks **Accept Quote**. Only one quotation may be accepted per Shipment — to switch, uncheck the first before accepting another. Accepting a quotation writes the carrier's quote ID to the Shipment and enables the **Schedule LTL Pickup** button.

Clicking **Schedule LTL Pickup** books the load with the carrier, writes the PRO/tracking number and internal booking reference to the Shipment, and attaches the Bill of Lading as a File record. Subsequent **Track Shipment** and **Get Documents** actions use those references.

Not all carriers support every action through the API. The interface only shows buttons for operations the selected provider supports, and displays a clear message when a function must be handled outside ERPNext (e.g. calling the carrier directly to schedule a pickup).

## Built-in Direct Carrier Providers

### WWEX (Worldwide Express / SpeedShip)

**Base URL:** `https://speedship.staging-wwex.com` (staging) or the production equivalent.

**Authentication:** OAuth 2.0 client-credentials via a Frappe **Connected App**. Set **Connected App** on the Freight Carrier Settings record. The Connected App's `query_parameters` child table must include `audience` set to the appropriate WWEX audience value (e.g. `staging-wwex-apig` for staging). Tokens are cached per Freight Carrier Settings record and refreshed automatically.

**Freight Carrier Settings fields required:**
- **Base URL** — WWEX SpeedShip endpoint
- **Connected App** — Frappe Connected App with WWEX OAuth credentials

**Workflow mapping:**

| Step | WWEX endpoint |
|---|---|
| Get quotes | `POST /svc/shopFlow` |
| Schedule pickup | `POST /svc/quoteOrderFlow` |
| Get documents | `POST /svc/documentDownloadFlow` |
| Track shipment | `POST /svc/searchShipmentsFlow` |
| Cancel shipment | `POST /svc/integratedCancelFlow` |

`quote_or_offer_id` stores the `shipmentOfferId`; `quote_or_offer_transaction_id` stores the `shipmentProductTransactionId`. Spot quotes are not supported — only contracted rate quotes via shopFlow.

**Accessorial services:** WWEX supports a broad set of accessorial flags mapped from Shipment fields (liftgate, inside delivery/pickup, residential, tradeshow, protect from cold, sort and segregate, insurance, construction site, hold at terminal, notify before delivery, direct delivery only, etc.).

---

### Banyan Technology (LIVE Connect v3)

**Base URL:** `https://ws.integration.banyantechnology.com/api/v3` (integration; include `/api/v3` so API paths resolve correctly) or the production equivalent.

**Authentication** (first match wins in code):
1. **LTL API Key** — if set, used as a static `Bearer` token on every request (skip token endpoint). Use only when Banyan gives you a long-lived token instead of OAuth client credentials.
2. **Client ID** + **Client Secret** on Freight Carrier Settings — **usual RIM/Banyan setup**: OAuth 2.0 `client_credentials` grant to `{host}/auth/connect/token` (host is derived from the Base URL; token is not under `/api/v3`).
3. **Connected App** — if the FCS record references one, its client ID/secret and `token_uri` are used for the token request instead of the FCS password fields.

**Freight Carrier Settings fields required:**
- **Base URL** — must contain `banyantechnology.com` (see Provider Selection)
- **Client ID** and **Client Secret** — default integration path; **or** **LTL API Key** alone for static Bearer; **or** **Connected App** if you wire Banyan through it

**Workflow mapping:**

| Step | Banyan endpoint |
|---|---|
| Get quotes | `POST /shipments` (with `waitForRates: true`) |
| Schedule pickup | `POST /shipments/{loadId}/book` |
| Get documents | `GET /shipments/{loadId}/documents` |
| Track shipment | `GET /tracking/statuses?loadId=...` |
| Cancel shipment | `POST /shipments/{loadId}/cancel` |

`quote_or_offer_id` stores the Banyan `quoteId` (integer); `quote_or_offer_transaction_id` stores the `loadId`. The `loadId` is used as the Shipment's `shipment_id` after booking. Spot quotes are not supported.

**Accessorial services:** Banyan supports a full set of accessorial codes (liftgate delivery/pickup, inside delivery/pickup, residential, appointment, construction site, limited access, tradeshow, sort and segregate, COD, notify before delivery, protect from cold, over-dimension, marked/tagged, etc.).

---

### TrafficTech (LTL Quoting API)

**Base URL:** UAT example: `https://apitest.traffictech.com/ltl-api-n8n/` (no query string stored on the row; the integration appends `message-type=LTLRate`). Production URL is issued when your account is approved.

**Authentication:** HTTP header `subscription-key` from **LTL API Key** on Freight Carrier Settings. TrafficTech also requires static outbound IP whitelisting before calls succeed.

**Freight Carrier Settings fields required:**
- **Base URL** — must contain `traffictech.com` (see Provider Selection)
- **LTL API Key** — subscription key
- **Account Number** — numeric TrafficTech `customerId`
- **Client ID** — TT Interactive portal email (`customerContactEmail` in the JSON body)
- **Client Secret** — TT Interactive portal password (`customerContactEmailPassword` in the JSON body)

**Workflow mapping:**

| Step | TrafficTech endpoint |
|---|---|
| Get quotes | `POST {base_url}?message-type=LTLRate` |
| Schedule pickup | *Not in current API guide — integration throws a clear message until TrafficTech documents booking* |
| Get documents / Track / Cancel | *Same — follow-on documentation expected from TrafficTech* |

`quote_or_offer_id` stores the `quoteId` from each carrier offer; `quote_or_offer_transaction_id` stores the response `loadId`. Hazmat is rejected at quote time (API requires `isHazmat: false`). Declared value defaults to **500** in the request when **Value of Goods** on the Shipment is unset (per TrafficTech sample payloads).

**Accessorial services:** A subset of Shipment checkboxes map to TrafficTech boolean flags (residential/inside/liftgate pickup and delivery, delivery appointment, notify before delivery, sort and segregate).

---

### ODFL (Old Dominion Freight Line)

**Base URL:** `https://api.odfl.com` (production) or `https://apiq.odfl.com` (QA).

**Authentication:** Two mechanisms are used depending on the API:
- **SOAP Rate API** — credentials sent inline in the SOAP body as `odfl4MeUser` / `odfl4MePassword` (mapped from **Client ID** / **Client Secret** on the FCS record).
- **REST APIs** (eBOL, pickup, tracking, documents) — a session Bearer token is obtained from `GET /auth/v1.0/token` using HTTP Basic auth with the same **Client ID** / **Client Secret**. Tokens have a 1-hour TTL and are cached per FCS record.

**Freight Carrier Settings fields required:**
- **Base URL** — ODFL API endpoint
- **Client ID** — ODFL username (`odfl4MeUser`)
- **Client Secret** — ODFL password (`odfl4MePassword`)

Country codes must be ISO 3166-1 alpha-3 (USA, CAN, MEX). The provider converts two-letter country codes from ERPNext addresses automatically.

**Workflow mapping:**

| Step | ODFL endpoint |
|---|---|
| Get quotes | SOAP `POST https://www.odfl.com/wsRate_v6/RateService` |
| Schedule pickup — step 1 | `POST /BOL/v3.1/eBOL/bol-external-per-standards` |
| Schedule pickup — step 2 | `POST /pickup/v3.0/create` |
| Get documents | `GET /document-retrieval-api/v1.0/getDocument` |
| Track shipment | `GET /tracking/v2.0/shipment.track` |
| Cancel — pickup | `DELETE /pickup/v3.0/cancel` |
| Cancel — BOL | `DELETE /BOL/v3.1/eBOL/deleteBolPro` |

`quote_or_offer_id` stores the ODFL `referenceNumber` from the SOAP rate response. After booking, `awb_number` holds the PRO number and `pickup_id` holds the pickup confirmation number. Cancellation requires both to be set.

**Accessorial services:** ODFL supports accessorials including liftgate delivery/pickup, inside delivery/pickup, residential, appointment, construction site, limited access, hazardous material, notify before delivery, protect from cold, over-dimension, and insurance.

## Adding a New Direct Carrier Provider

The `ltl_providers` hook in `hooks.py` maps a **Base URL domain substring** to the dotted import path of a `BaseLTL` subclass. To register a new provider:

1. Create a class that inherits from `BaseLTL` (defined in `base_ltl.py`) and implements the required methods.

```py
# in custom_app/path/to/my_carrier.py
from shipstation_integration.base_ltl import BaseLTL

class MyCarrierLTL(BaseLTL):
    def get_ltl_quotes(self, doc, settings_name=None): ...
    def schedule_ltl_pickup(self, doc, settings_name=None): ...
    # implement remaining BaseLTL methods
```

2. Add an entry to `ltl_providers` in your app's `hooks.py`. Entries are matched in definition order — if you need to override a built-in entry, place yours before it.

```py
# in hooks.py
ltl_providers = {
    "mycarrier.com": "custom_app.path.to.my_carrier.MyCarrierLTL",
}
```

3. Create a **Freight Carrier Settings** record with **Base URL** containing the substring you registered (e.g. `https://api.mycarrier.com`). Set whichever credential fields the provider requires.

The fallback when no entry matches remains `ShipstationLTL` (ShipEngine).

- Add a hook in `hooks.py` with the "ltl" key set to the method string of the function that returns the overriding class. The Shipstation Integration app looks for this hook first when returning the LTL class

```py
# in hooks.py

# Shipstation Override
override_shipstation = {
    "ltl": "custom_app.path.to.python_script.get_ltl_override_class",
}
```
