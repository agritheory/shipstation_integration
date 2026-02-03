## Gap Analysis Summary

### 1. Parcels: Storing Carrier and Dimensions for Reuse

**Current State:**
- The codebase customizes ERPNext's standard `Shipment Parcel` doctype (labels for length/width/height/weight)
- In `rates.py`, package dimensions are **hardcoded** to defaults (12×9×6 inches) when fetching rates for a Delivery Note:

```218:230:shipstation_integration/rates.py
	# Package with weight and dimensions (dimensions required by some carriers like FedEx)
	# TODO: In the future, pull actual dimensions from Shipment Parcel if available
	packages = [
		{
			"weight": {"value": total_weight, "unit": "pound"},
			"dimensions": {
				"length": 12,  # Default dimensions in inches
				"width": 9,
				"height": 6,
				"unit": "inch",
			},
		}
	]
```

**Gaps to Address:**
1. **No Parcel Template DocType** - Need a way to define reusable parcel/box types with:
   - Name/description
   - Carrier (optional, for carrier-specific packages)
   - Dimensions (L×W×H)
   - Max weight
   - Package code (for carrier-specific codes like "fedex_pak", "ups_medium_box")
   
2. **No link from Items to Parcels** - Items don't have suggested packaging dimensions
   
3. **No UI to select parcels during shipping** - The shipping label dialog (`shipping.js`) has a package type dropdown but it just passes carrier package codes, not dimensions

4. **Multi-package shipments** - Current implementation only supports single package per shipment in both `labels.py` and `rates.py`

**Recommendation for team:** Create a new `Shipping Parcel Template` DocType, add a Link field on Shipment Parcel to reference it, and update `_build_shipment_from_delivery_note()` and `get_rates_for_delivery_note()` to use actual dimensions.

---

### 2. Third-Party Carrier Codes (Bill to Third Party)

**Current State:**
- The OpenAPI spec (`.llm/openapi.json`) shows ShipEngine supports these `advanced_options`:

```8053:8089:.llm/openapi.json
          "bill_to_account": {
            "description": "This field is used to [bill shipping costs to a third party]..."
          },
          "bill_to_country_code": {...},
          "bill_to_party": {...},  // "third_party"
          "bill_to_postal_code": {...},
```

- Neither `labels.py` nor `rates.py` pass any `advanced_options` to the API
- There's a `bill_to` field in `customer.py` but this is billing **address**, not third-party billing

**Gaps to Address:**
1. **No data model for third-party billing accounts** - Need to store:
   - Third-party account numbers (per carrier or customer)
   - Bill-to party postal code
   - Bill-to country code
   - Carrier restrictions (not all carriers support this)

2. **No fields on Delivery Note/Shipment** to capture:
   - Use third-party billing (checkbox)
   - Third-party account number
   - Third-party postal code
   - Third-party country code

3. **No code to pass `advanced_options`** - Both `_build_shipment_from_delivery_note()` in `labels.py` and `get_rates()` in `rates.py` need to accept and pass `advanced_options`

**Recommendation for team:** 
- Add custom fields to Customer or Address for storing third-party account info
- Add fields to Delivery Note for selecting third-party billing
- Update `_build_shipment_from_delivery_note()` and `_format_package()` to include `advanced_options` when third-party billing is specified

---

### 3. Quotation Workflow (LTL and Freight APIs)

**Current State:**
- **Zero LTL implementation exists** - searching the codebase for "ltl", "freight", "bol" shows no implementation
- The OpenAPI spec references LTL endpoints but none are used:
  - `freight_class`
  - `fedex_freight`
  - `use_ups_ground_freight_pricing`
- Current `rates.py` only handles small parcel rate shopping

**Gaps to Address (per issue #61):**

1. **LTL Carrier Connection**
   - No method to connect LTL carrier accounts
   - Need `connect_ltl_carrier()` function
   - LTL carriers have different credential requirements than small parcel

2. **LTL Quote Request**
   - No function to request LTL quotes
   - Requires different payload structure:
     - Freight class per item
     - Accessorial services (liftgate, inside delivery, etc.)
     - Container types
     - Service levels

3. **Spot Quote Support**
   - LTL often requires spot quotes for unusual freight
   - No implementation

4. **BOL (Bill of Lading) Generation**
   - Critical for LTL shipments
   - Requires pickup scheduling first
   - No implementation

5. **LTL Tracking**
   - Different tracking structure than small parcel
   - PRO numbers vs tracking numbers
   - No implementation

6. **ERPNext Quotation Integration**
   - Rate quotes should optionally save to ERPNext Quotation doctype
   - Allow converting quote to shipment
   - Track quote validity/expiration

**Recommendation for team:** This is a significant undertaking. Suggest creating:
- New `LTL Carrier Account` DocType
- New `Freight Quote` DocType to track LTL quotes
- New `ltl.py` module with functions mirroring the ShipEngine LTL API
- Add `freight_class` field to Item doctype
- Consider whether this belongs in a separate phase/release

---

### 4. Label Generation via ShipEngine API v2

**Current State - This is largely working:**

`labels.py` has these implemented functions:
- `create_label()` - direct label creation
- `create_label_from_rate()` - from rate_id (working)
- `void_label()` - label voiding
- `get_label()` - label retrieval  
- `create_label_for_delivery_note()` - integrated workflow
- `create_return_label()` - from existing outbound label

`shipping.py` routes to v2 when `rate_id` is provided:

```66:69:shipstation_integration/shipping.py
	if use_v2:
		_create_shipping_label_v2(doc_dict, values_dict, user=frappe.session.user)
	else:
		_create_shipping_label(doc, values, user=frappe.session.user)
```

**Gaps to Address:**

1. **Hardcoded dimensions** (ties to Task #1) - `_build_shipment_from_delivery_note()` doesn't pull parcel dimensions

2. **Hardcoded label format**:

```77:82:shipstation_integration/labels.py
		label_params = {
			"label_format": "pdf",
			"label_layout": "4x6",
		}
```

3. **No multi-package support** - single package array only

4. **No third-party billing** (ties to Task #2) - no `advanced_options` passed

5. **Direct label creation (no rate_id)** needs work - `create_label()` exists but `_create_shipping_label_v2()` only calls `create_label_for_delivery_note()` which expects either rate_id OR carrier_id+service_code

**Recommendation for team:** Label generation is ~80% complete. Main work is integrating with parcel templates and third-party billing once those are built.

---

### 5. Documentation Gap

**Current State:**
- README has basic installation and feature list
- No detailed user or developer documentation

**Critical Documentation Needs:**
1. **Configuration Guide** - Step-by-step setup for v1, v2, or both
2. **Workflow Documentation** - How to use rate shopping, label generation, fulfillment sync
3. **API Reference** - Document all whitelisted functions in `rates.py`, `labels.py`, `fulfillments.py`, `shipping.py`
4. **v1 vs v2 Decision Guide** - When to use which API
5. **Troubleshooting Guide** - Common errors and solutions
6. **Developer Guide** - How to extend the integration

---

## Summary Table

| Task | Completeness | Effort Estimate | Dependencies |
|------|-------------|-----------------|--------------|
| 1. Parcels | ~10% | Medium | None |
| 2. Third-party billing | 0% | Medium | None |
| 3. LTL/Freight | 0% | High | Tasks 1 & 2 helpful |
| 4. Label generation (v2) | ~80% | Low | Tasks 1 & 2 to complete |
| 5. Documentation | ~20% | Medium | All tasks ideally |
