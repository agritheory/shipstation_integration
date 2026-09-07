# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

"""Enable Inventory Tools Alternative Sales Workflow for every Shipstation Settings company."""

from shipstation_integration.shipstation_integration.overrides.sales_order_context import (
	ensure_alternative_sales_workflow_for_all_shipstation_settings,
)


def execute():
	ensure_alternative_sales_workflow_for_all_shipstation_settings()
