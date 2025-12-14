<div align="center" markdown="1">

<a href="https://github.com/AgriTheory/shipstation_integration">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="./.github/assets/shipstation-logo_dark.svg">
    <img alt="ShipStation" src="./.github/assets/shipstation-logo_light.svg" width="200">
  </picture>
</a>

# ShipStation Integration for ERPNext

**Seamless shipping, label generation, and order fulfillment for ERPNext**

[![MIT License][license-shield]][license-url]

<p align="center">
  <br />
  <a href="https://github.com/AgriTheory/shipstation_integration/issues/new?labels=bug">Report Bug</a>
  ·
  <a href="https://github.com/AgriTheory/shipstation_integration/issues/new?labels=enhancement">Request Feature</a>
</p>

</div>

<a id="readme-top"></a>

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li><a href="#about-the-project">About The Project</a></li>
    <li><a href="#installation">Installation</a></li>
    <li><a href="#configuration">Configuration</a>
      <ul>
        <li><a href="#api-options">API Options</a></li>
      </ul>
    </li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
  </ol>
</details>

<!-- ABOUT THE PROJECT -->
## About The Project

ShipStation Integration connects your ERPNext instance with [ShipStation](https://www.shipstation.com/) for streamlined order management, shipping label generation, and fulfillment tracking. It supports both the classic ShipStation API (v1) and the newer ShipStation API v2 for enhanced carrier access and rate shopping.

### Features

**Multi-Account & Store Management**
- Sync multiple ShipStation accounts with a single ERPNext instance
- Configure individual stores with different companies, warehouses, cost centers, and account heads
- Periodically fetch products, orders, and shipments from all connected accounts
- Amazon marketplace integration with hooks for custom order processing

**Shipping & Labels (v1)**
- Shipping label generation directly from Delivery Notes
- Configurable per ShipStation account

**Rate Shopping & Labels (v2)**
- Compare rates across multiple carriers before purchasing
- Direct label generation without requiring orders in ShipStation
- Rate shopping UI in Delivery Notes — view pricing and select the best option
- Void labels when needed

**Fulfillment Sync (v2)**
- Push fulfillment data back to connected marketplaces
- Automatic tracking number updates

**Webhooks**
- v1 store-based webhooks for order and shipment events
- v2 environment-based webhooks for batch completion and tracking updates

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- INSTALLATION -->
## Installation

1. Navigate to your bench directory
   ```bash
   cd frappe-bench
   ```

2. Get the app
   ```bash
   bench get-app https://github.com/AgriTheory/shipstation_integration
   ```

3. Install the app on your site
   ```bash
   bench --site your-site.localhost install-app shipstation_integration
   ```

4. Run migrations
   ```bash
   bench --site your-site.localhost migrate
   ```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- CONFIGURATION -->
## Configuration

1. Navigate to **Shipstation Settings** in ERPNext
2. Enter your ShipStation API credentials (v1: API Key + Secret)
3. Optionally enable ShipStation API v2 and enter your v2 API key for rate shopping and direct label generation
4. Configure your stores, linking each to the appropriate company and warehouse
5. Set up webhooks to receive order and shipment updates

### API Options

This integration supports the classic ShipStation API (v1), the newer ShipStation API v2, or both together. You can choose based on your needs:

**v1 Only** — Use if you need to sync orders from ShipStation-connected marketplaces (Amazon, Shopify, etc.) and generate labels through ShipStation's order workflow.

**v2 Only** — Use if you just need to ship packages and compare rates without marketplace integrations. Labels can be purchased directly without orders existing in ShipStation.

**Both** — Get the full feature set: order management flows through v1, while v2 provides rate shopping and direct label purchase capabilities.

| Feature | v1 Only | v2 Only | Both |
|---------|:-------:|:-------:|:----:|
| Order sync from marketplaces | ✓ | | ✓ |
| Auto-shipment creation | ✓ | | ✓ |
| Store management | ✓ | | ✓ |
| Rate shopping before purchase | | ✓ | ✓ |
| Direct label purchase | | ✓ | ✓ |
| Fulfillment push to marketplaces | | ✓ | ✓ |
| Tags & product import | ✓ | | ✓ |

To configure:
- **v1**: Check `Enabled` and enter your `API Key` + `API Secret`
- **v2**: Check `Enable ShipStation API v2` and enter your `ShipStation API Key`

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- CONTRIBUTING -->
## Contributing

Contributions are welcome! Please see the [contribution guidelines](CONTRIBUTING.md) for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- LICENSE -->
## License

Distributed under the MIT License. See `license.txt` for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- MARKDOWN LINKS & IMAGES -->
[license-shield]: https://img.shields.io/badge/License-MIT-green?style=flat
[license-url]: https://github.com/AgriTheory/shipstation_integration/blob/main/license.txt
