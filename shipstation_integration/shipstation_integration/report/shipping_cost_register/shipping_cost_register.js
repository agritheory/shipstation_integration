// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.query_reports['Shipping Cost Register'] = {
	filters: [
		{
			fieldname: 'from_date',
			label: __('From Date'),
			fieldtype: 'Date',
			reqd: 1,
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -180),
		},
		{
			fieldname: 'to_date',
			label: __('To Date'),
			fieldtype: 'Date',
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: 'company',
			label: __('Company'),
			fieldtype: 'Link',
			options: 'Company',
			default: frappe.defaults.get_user_default('Company'),
		},
		{
			fieldname: 'shipment_type',
			label: __('Type'),
			fieldtype: 'Select',
			options: ['All', 'Parcel', 'LTL', 'Full Truckload', 'Container'],
			default: 'All',
		},
		{
			fieldname: 'customer',
			label: __('Customer'),
			fieldtype: 'Link',
			options: 'Customer',
		},
		{
			fieldname: 'carrier',
			label: __('Carrier'),
			fieldtype: 'Data',
		},
	],
}
