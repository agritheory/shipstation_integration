# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

import json
from pathlib import Path
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.utils import get_bench_path


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

	# Setup test data once per session
	from shipstation_integration.tests.setup import create_test_data

	create_test_data()

	yield frappe.db


@pytest.fixture
def mock_shipstation_settings(monkeypatch):
	"""Create a mocked ShipStation Settings document with API client."""
	import json

	mock_settings = MagicMock()
	mock_settings.name = "ShipStation Settings"
	mock_settings.enabled = 1
	mock_settings.enable_shipstation_api = 1
	mock_settings.api_key = "test_api_key_12345"

	# Mock the API client
	mock_client = MagicMock()
	mock_settings.shipstation_api_client.return_value = mock_client

	# Set carrier data for rate requests
	mock_settings.shipstation_api_carrier_data = json.dumps(
		[
			{"carrier_id": "se-123", "carrier_code": "usps"},
			{"carrier_id": "se-456", "carrier_code": "fedex"},
			{"carrier_id": "se-789", "carrier_code": "ups"},
		]
	)

	# Patch all imports of get_shipstation_settings
	monkeypatch.setattr(
		"shipstation_integration.utils.get_shipstation_settings", lambda *args, **kwargs: mock_settings
	)
	monkeypatch.setattr(
		"shipstation_integration.labels.get_shipstation_settings", lambda *args, **kwargs: mock_settings
	)
	monkeypatch.setattr(
		"shipstation_integration.rates.get_shipstation_settings", lambda *args, **kwargs: mock_settings
	)

	return mock_settings


@pytest.fixture
def mock_settings_with_client(mock_shipstation_settings):
	"""Alias for backwards compatibility."""
	return mock_shipstation_settings
