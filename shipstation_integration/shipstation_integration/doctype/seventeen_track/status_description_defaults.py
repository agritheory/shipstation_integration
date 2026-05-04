# Copyright (c) 2026, AgriTheory and contributors
# For license information, please see license.txt


def base_status_description_map() -> dict[tuple[str, str], str]:
	"""Default (status, sub_status) -> human-readable description; registered via hooks."""
	return {
		("NotFound", "NotFound_Other"): "The carrier didn't return any message.",
		("NotFound", "NotFound_InvalidCode"): "The tracking number is invalid.",
		("InfoReceived", "InfoReceived"): "",
		("InTransit", "InTransit_PickedUp"): "The carrier has collected the package from the sender.",
		("InTransit", "InTransit_Other"): "Other circumstances beyond the currently known sub-statuses.",
		("InTransit", "InTransit_Departure"): "Package has left the originating country/region's port.",
		(
			"InTransit",
			"InTransit_Arrival",
		): "Package has arrived at the destination country/region's port.",
		(
			"InTransit",
			"InTransit_CustomsProcessing",
		): "Your shipment is under the customs clearance process.",
		("InTransit", "InTransit_CustomsReleased"): "Import/Export customs clearance is completed.",
		(
			"InTransit",
			"InTransit_CustomsRequiringInformation",
		): "Related information is required for clearance.",
		("Expired", "Expired_Other"): "",
		("AvailableForPickup", "AvailableForPickup_Other"): "",
		("OutForDelivery", "OutForDelivery_Other"): "",
		(
			"DeliveryFailure",
			"DeliveryFailure_Other",
		): "Other circumstances beyond the currently known sub-statuses.",
		(
			"DeliveryFailure",
			"DeliveryFailure_NoBody",
		): "Unable to contact the recipient temporarily during the delivery process, resulting in delivery failure.",
		(
			"DeliveryFailure",
			"DeliveryFailure_Security",
		): "Package encountered security, customs clearance, or fee issues during delivery, resulting in delivery failure.",
		(
			"DeliveryFailure",
			"DeliveryFailure_Rejected",
		): "Recipient refused to accept the package for certain reasons, resulting in delivery failure.",
		(
			"DeliveryFailure",
			"DeliveryFailure_InvalidAddress",
		): "Delivery failure due to an incorrect recipient address.",
		("Delivered", "Delivered_Other"): "",
		("Exception", "Exception_Other"): "Other circumstances beyond the currently known sub-statuses.",
		("Exception", "Exception_Returning"): "Package is being returned to the sender.",
		("Exception", "Exception_Returned"): "Sender has successfully received the returned package.",
		(
			"Exception",
			"Exception_NoBody",
		): "Cannot find the recipient due to the abnormal recipient information discovered before delivery.",
		(
			"Exception",
			"Exception_Security",
		): "Abnormalities found before delivery, including security, customs clearance, or fee issues.",
		(
			"Exception",
			"Exception_Damage",
		): "The package was found damaged during the transportation process.",
		("Exception", "Exception_Rejected"): "Recipient refused to accept the package before delivery.",
		(
			"Exception",
			"Exception_Delayed",
		): "Possible delay beyond the original scheduled transit time due to various circumstances.",
		("Exception", "Exception_Lost"): "Package lost due to various circumstances.",
		(
			"Exception",
			"Exception_Destroyed",
		): "Package unable to be delivered for various reasons and subsequently destroyed.",
		("Exception", "Exception_Cancel"): "Shipment order was cancelled due to various circumstances.",
	}
