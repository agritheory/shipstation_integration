# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "shipstation_integration/1.0 (support@agritheory.dev)"


def nominatim_geocode(address: dict) -> tuple[float, float] | None:
	"""
	Default geocoding implementation using Nominatim (OpenStreetMap).

	Receives an address dict with keys: street, city, state, postal_code, country, location.
	Returns (latitude, longitude) or None if not found.

	Nominatim usage policy: max 1 request/second, no bulk use in production.
	For high-volume use, register a seventeen_track_geocode_address hook in your app
	that calls a commercial geocoding service instead.
	"""
	query = build_geocode_query(address)
	if not query:
		return None

	try:
		response = requests.get(
			NOMINATIM_URL,
			params={"q": query, "format": "json", "limit": 1},
			headers={"User-Agent": NOMINATIM_USER_AGENT},
			timeout=5,
		)
		response.raise_for_status()
		results = response.json()
	except Exception:
		return None

	if not results:
		return None

	try:
		return float(results[0]["lat"]), float(results[0]["lon"])
	except (KeyError, ValueError, TypeError):
		return None


def build_geocode_query(address: dict) -> str:
	"""Build a search query string from address parts, falling back to raw location."""
	parts = [
		address.get("street") or "",
		address.get("city") or "",
		address.get("state") or "",
		address.get("postal_code") or "",
		address.get("country") or "",
	]
	query = ", ".join(p for p in parts if p)
	if query:
		return query
	# Fall back to the raw location string (e.g. "GASQUET, CA, US")
	return address.get("location") or ""
