# Copyright (c) 2025, AgriTheory and contributors
# For license information, please see license.txt

import json
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.utils import get_bench_path

from shipstation_integration.shipstation_integration.doctype.shipstation_settings.shipstation_settings import (
	ShipstationSettings,
)


def get_logger(*args, **kwargs):
	from frappe.utils.logger import get_logger

	return get_logger(
		module=None,
		with_more_info=False,
		allow_site=True,
		filter=None,
		max_size=100_000,
		file_count=20,
		stream_only=True,
	)


@pytest.fixture(scope="module")
def monkeymodule():
	with pytest.MonkeyPatch.context() as mp:
		yield mp


@pytest.fixture(scope="session", autouse=True)
def db_instance():
	frappe.logger = get_logger

	currentsite = "test_site"
	sites = Path(get_bench_path()) / "sites"
	if (sites / "common_site_config.json").is_file():
		currentsite = json.loads((sites / "common_site_config.json").read_text()).get("default_site")

	frappe.init(site=currentsite, sites_path=sites)
	frappe.connect()
	frappe.db.commit = MagicMock()

	# Same idempotent tail as before_test (after create_test_data): local pytest may not run bench execute.
	from shipstation_integration.tests.setup import (
		create_seventeen_track_settings,
		create_test_tracking_numbers,
		ensure_ambrosia_shipstation_gs1_prefix,
		ensure_draft_shipment_pickup_dates_current,
	)

	company = frappe.defaults.get_global_default("default_company") or "Ambrosia Pie Company"
	create_seventeen_track_settings(company)
	create_test_tracking_numbers()
	ensure_ambrosia_shipstation_gs1_prefix()
	ensure_draft_shipment_pickup_dates_current()
	yield frappe.db


@pytest.fixture
def shipstation_api_client_mock(monkeypatch):
	client = MagicMock()

	def patched_shipstation_api_client(self):
		return client

	monkeypatch.setattr(ShipstationSettings, "shipstation_api_client", patched_shipstation_api_client)
	return client
