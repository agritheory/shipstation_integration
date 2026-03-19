<!-- Copyright (c) 2026, AgriTheory and contributors
For license information, please see license.txt-->

# Less Than Truckload (LTL)

<div class="byline">
  Heather Kusmierz 2026-03-09
</div>


Shipstation Integration app integrates Shipstation's less-than-truckload (LTL) functionality into ERPNext's Shipment document.

## Configuration

Before starting, the company's Shipstation account that is used in ERPNext should be connected to any LTL carriers that the company expects to use.

Once LTL carriers are connected, the remaining configuration is done in the Shipstation Settings document. First, enable Shipstation API v2 and fill in the API key. The Base URL, Alternative Client ID, and Alternative Client Secret fields are there if the company wants to override Shipstation's LTL functionality with another shipping provider's API - see the "Override Shipstation's LTL with Another API" section for more information. If everything is connected properly, clicking the "Fetch LTL Carriers" button will collect and display summary information about each connected carrier.

## LTL Shipment Workflow

Shipstation's LTL integration starts from ERPNext's Shipment document, when the Freight Type field is set to "LTL" and a Preferred Carrier is selected. (Shipstation requires a carrier when requesting quotes or spot quotes). Once the pickup from, delivery to, package, service level, and billing information fields are ready, click on "Get LTL Quotes" button to request contracted rate quotes from the selected carrier. If the carrier offers spot quotes, the user will see a check box to request them instead of regular quotes when clicking the button.

The app will automatically store all quotes in Shipment Quotation documents with their pertinent information, then display a summary list to the user. When ready, the user can check Accept Quote in the chosen Shipment Quotation document. Only one quote may be accepted for a Shipment - if the user wants to accept a different one, they must first un-check the Accept Quote box on the first before the system will let them accept the new one. Accepting a quote automatically populates the Quote ID into the Shipment document, which enables the user to see the Schedule LTL Pickup button.

Note that not all carriers offer the same functionality through the API - some may not offer spot quotes, or some may require you call or contact the carrier directly to schedule a pickup vs doing it through ERPNext via an API call. The Shipstation Integration interface is designed to only show available options and display a clear message to the user when a function isn't supported by the chosen carrier.

When the successfully schedules a pickup by clicking the Schedule LTL Pickup button, ERPNext automatically populates a number of related fields, including the tracking/PRO Number field, and will try to save and attach all generated documents, such as the Bill of Lading.


## Override Shipstation's LTL with Another API

The Shipstation Integration app supports using an alternative LTL provider's API instead of Shipstation's. There's a "BaseLTL" class defined in `base_ltl.py` that lays out the structure and necessary functionality the overriding class should have. The steps to implement an overriding LTL class are as follows:

- Create a Python script in your app to hold a function that returns your overriding class and the class definition itself. The new class should inherit from Shipstation Integration's `BaseLTL` class, which serves as a template for the minimum required functionality for it to work in the app. The below is an example structure - the path, filename, and function names can be anything.

```py
# in custom_app/path/to/python_script.py
from shipstation_integration.base_ltl import BaseLTL


def get_ltl_override_class():
    # This function ties to hooks.py to return the override class
    return OtherLTL()


class OtherLTL(BaseLTL):
    # At a minimum, implement the BaseLTL methods for the overriding API
    pass
```

- Add a hook in `hooks.py` with the "ltl" key set to the method string of the function that returns the overriding class. The Shipstation Integration app looks for this hook first when returning the LTL class

```py
# in hooks.py

# Shipstation Override
override_shipstation = {
    "ltl": "custom_app.path.to.python_script.get_ltl_override_class",
}
```
