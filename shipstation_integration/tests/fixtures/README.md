# Test Fixtures

This directory contains captured API responses for testing.

## ShipStation Packages Response

The `shipstation_packages_response.json` file contains real API responses from ShipStation's `/v2/packages` endpoint, captured and anonymized for use in tests.

### Response Structure

```json
{
  "captured_responses": [
    {
      "test_case": "successful_package_creation",
      "timestamp": "2026-02-08 10:21:30.590434",
      "response": {
        "status_code": 200,
        "url": "https://api.shipstation.com/v2/packages",
        "request_payload": { ... },
        "headers": { "API-Key": "REDACTED" },
        "response_body": { ... }
      }
    }
  ]
}
```

### Sensitive Data

All sensitive fields have been anonymized:
- API keys are redacted
- Account IDs are redacted (if present)
- User IDs are redacted (if present)

## Using Captured Fixtures in Tests

Once you have captured responses, you can write mocked tests that use them:

```python
def test_with_captured_response(fixtures_dir):
    fixture_file = fixtures_dir / "shipstation_packages_response.json"
    with open(fixture_file) as f:
        fixtures = json.load(f)
    
    # Find the specific test case
    response_data = next(
        (r for r in fixtures["captured_responses"] 
         if r["test_case"] == "successful_package_creation"),
        None
    )
    
    # Use response_data to mock API calls
    # ...
```

## Fixture File Format

```json
{
  "captured_responses": [
    {
      "test_case": "successful_package_creation",
      "timestamp": "2024-02-08 12:34:56",
      "response": {
        "status_code": 201,
        "url": "https://api.shipstation.com/v2/packages",
        "request_payload": { ... },
        "headers": { "API-Key": "REDACTED" },
        "response_body": { ... }
      }
    }
  ]
}
```
