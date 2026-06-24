<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Example Data

<div class="byline">
  AgriTheory 2026-06-24
</div>


The ShipStation Integration app includes a test setup script that populates an ERPNext site with demo business data. Use it on a dedicated test site to experiment with order sync, labels, LTL, cartonization, and 17Track features before configuring production credentials.

## Prerequisites

Install these apps on the site before running the setup script:

- **ERPNext**
- **BEAM** — required for handling units and Packing Slip workflows
- **Inventory Tools** — required for cartonization test fixtures

See the [installation instructions](https://github.com/AgriTheory/shipstation_integration) in the repository README.

## Running the setup script

With `bench start` running in the background:

```shell
bench execute 'shipstation_integration.tests.setup.before_test'
```

If you use `bench use your-site.localhost`, you can omit the `--site` flag:

```shell
bench execute shipstation_integration.tests.setup.before_test
```

The script:

1. Runs ERPNext `setup_complete` for **Ambrosia Pie Company**
2. Loads BEAM test data
3. Creates Shipstation Settings, stores, carriers, and sample tracking records
4. Seeds 17Track settings and test tracking numbers

## When to re-run

Re-run after:

- A fresh site install or `bench reinstall`
- Schema changes that require updated fixtures
- Before running pytest (same as CI)

To reinstall from scratch:

```shell
bench reinstall --yes --admin-password admin --mariadb-root-password admin
bench execute 'shipstation_integration.tests.setup.before_test'
```

## Running tests

After seeding, run pytest from the app directory. The README [Running tests](https://github.com/AgriTheory/shipstation_integration#running-tests) section covers BEAM troubleshooting and common fixture errors.

## Configuration

The setup script creates a **Shipstation Settings** document with test credentials and sample feature toggles enabled. Replace these with your own ShipStation API keys before connecting to a live account.
