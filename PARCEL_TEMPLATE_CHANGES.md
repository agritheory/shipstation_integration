# Parcel Template Changes Summary

## Overview

Added comprehensive test coverage for Shipment Parcel Template customizations and a new field to control ShipStation synchronization.

## Changes Made

### 1. Fixed Import Bug

**File:** `shipstation_integration/shipstation_integration/overrides/shipment_parcel_template.py`

- Fixed broken import: `from shipstation_integration.carriers import _get_settings` 
- Changed to: `from shipstation_integration.utils import get_shipstation_settings`
- Updated function call from `_get_settings(None)` to `get_shipstation_settings(None)`

### 2. Added New Custom Field

**File:** `shipstation_integration/shipstation_integration/custom/shipment_parcel_template.json`

Added `skip_shipstation_sync` checkbox field:
- **Label:** "Skip ShipStation Sync"
- **Type:** Check
- **Description:** "Enable for pallets, freight, or other packages that should not sync to ShipStation"
- **Position:** After `weight` field, before column break

**Use Case:** Allows marking parcel templates (like pallets, freight containers, or custom shipping scenarios) that should not be synchronized with ShipStation's package catalog.

### 2.1. Made Package Code Conditionally Required

**File:** `shipstation_integration/shipstation_integration/custom/shipment_parcel_template.json`

Updated `package_code` field:
- Changed from always required (`reqd: 1`) to conditionally required
- Added `mandatory_depends_on: "eval:!doc.skip_shipstation_sync"`
- Now only required when `skip_shipstation_sync` is NOT checked
- Added helpful description: "Required for ShipStation sync - must be unique"

**Benefit:** Templates marked to skip sync (like pallets or freight) don't need a package code, reducing unnecessary data entry.

### 3. Updated Sync Logic

**File:** `shipstation_integration/shipstation_integration/overrides/shipment_parcel_template.py`

Modified `sync_parcel_template()` function to:
- Check if `skip_shipstation_sync` is enabled
- Raise an error if sync is attempted on a skipped template
- Prevents accidental syncing of packages that shouldn't be in ShipStation

### 4. Updated Client Script

**File:** `shipstation_integration/public/js/shipment_parcel_template.js`

Modified the refresh handler to:
- Only show "Sync to ShipStation" button if `skip_shipstation_sync` is not checked
- Improves UX by hiding the sync option for templates that shouldn't sync

### 5. Comprehensive Test Suite

**File:** `shipstation_integration/tests/test_parcel_template.py`

Added test coverage for:

#### Validation Tests
- `test_sync_validation_missing_dimensions()` - Ensures dimensions are required
- `test_sync_validation_missing_package_code()` - Ensures package code is required (when sync enabled)
- `test_sync_skipped_when_flag_set()` - Verifies skip flag is respected
- `test_package_code_not_required_when_sync_skipped()` - Verifies package code is optional when sync is disabled

#### Functionality Tests
- `test_package_code_custom_prefix()` - Verifies `custom_` prefix is added to package codes
- `test_sync_parcel_template_success()` - Tests successful package creation with mocked API
- `test_sync_api_error_handling()` - Tests error handling for API failures
- `test_uom_conversion_length()` - Tests UOM conversion logic (placeholder for future expansion)

#### Test Infrastructure
- Uses captured real ShipStation API responses for realistic mocking
- Fixtures stored in `fixtures/shipstation_packages_response.json`
- All sensitive data (API keys, IDs) anonymized

### 6. Test Fixtures

**Directory:** `shipstation_integration/tests/fixtures/`

- `shipstation_packages_response.json` - Captured and anonymized ShipStation API responses
- `README.md` - Documentation for the fixture format

## Benefits

1. **Bug Fix:** Resolved import error that would have caused runtime failures
2. **Flexibility:** Users can now mark certain parcel types to skip ShipStation sync
3. **Reduced Data Entry:** Package code not required for templates that don't sync (pallets, freight, etc.)
4. **Test Coverage:** Comprehensive tests ensure functionality works as expected
5. **Maintainability:** Tests use real captured responses for accurate API behavior
6. **Safety:** Prevents accidental syncing of inappropriate package types

## Usage

### Marking a Template to Skip Sync

1. Open a Shipment Parcel Template
2. Check "Skip ShipStation Sync" checkbox
3. The "Package Code" field is no longer required (can be left empty)
4. Save the template
5. The "Sync to ShipStation" button will no longer appear

**Example Use Cases:**
- Pallet: Check skip sync, no package code needed
- Freight/LTL: Check skip sync, no package code needed
- Custom container: Check skip sync, no package code needed
- Standard box: Leave unchecked, package code required for ShipStation sync

### Running Tests

```bash
# Run all parcel template tests
pytest shipstation_integration/tests/test_parcel_template.py -v

# Run a specific test
pytest shipstation_integration/tests/test_parcel_template.py::test_sync_parcel_template_success -v
```

## Migration Notes

- The new `skip_shipstation_sync` field defaults to unchecked (0)
- Existing parcel templates will continue to sync normally
- No data migration required
