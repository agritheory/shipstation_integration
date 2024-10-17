# Copyright (c) 2024, AgriTheory and contributors
# For license information, please see license.txt

import os
import subprocess

import frappe
from frappe.installer import update_site_config


def get_user_confirmation():
	while True:
		user_input = (
			input(
				"Adding custom queue for Shipstation Integration. This requires to run 'bench setup supervisor', do you want to run it? (yes/no): "
			)
			.strip()
			.lower()
		)
		if user_input in ["yes", "y"]:
			return True
		elif user_input in ["no", "n"]:
			return False
		else:
			print("Please enter 'yes' or 'no'.")


def add_custom_queue():
	sites_path = os.getcwd()
	common_site_config_path = os.path.join(sites_path, "common_site_config.json")
	workers = frappe.conf.workers

	if workers and "shipstation" in workers.keys():
		return

	if workers:
		workers["shipstation"] = {"timeout": 8000}
	else:
		workers = {"shipstation": {"timeout": 8000}}

	update_site_config("workers", workers, validate=False, site_config_path=common_site_config_path)

	# skip supervisor setup on development setups
	if not (frappe.conf.restart_supervisor_on_update or frappe.conf.restart_systemd_on_update):
		return

	if not get_user_confirmation():
		print("Please run 'bench setup supervisor' manually.")
		return

	process = subprocess.Popen(
		"bench setup supervisor --yes",
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
	)
	stdout, stderr = process.communicate()

	if process.returncode != 0:
		if "INFO: A newer version of bench is available" not in stderr:
			print(f"Command failed: {stderr}.")
		else:
			print(f"Command failed: {stdout}.")


def after_install():
	add_custom_queue()
