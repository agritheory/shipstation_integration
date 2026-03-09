# Less Than Truckload (LTL)

## Override Shipstation's LTL with Another API

The Shipstation Integration app supports using an alternative LTL provider's API instead of Shipstation's. There's a "BaseLTL" class defined in `base_ltl.py` that lays out the structure and necessary functionality the overriding class should have. The steps to implement an overriding LTL class are as follows:

- Create a Python script in your app to hold a function that returns your overriding class and the class definition itself. The new class should inherit from Shipstation Integration's `BaseLTL` class, which serves as a template for all necessary methods. The below is an example structure - the path, filename, and function names can be anything.

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
