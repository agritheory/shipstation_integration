// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Seventeen Track', {
	refresh(frm) {
		const btn = frm.add_custom_button('Check Quota', function () {
			frappe.call({
				method: 'shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track.get_quota',
				callback: function (r) {
					if (!r.message) return
					const d = r.message
					frappe.msgprint({
						title: '17Track Quota',
						indicator: 'blue',
						message: `
							<table class="table table-bordered" style="margin-top:8px">
								<tr><td>Total</td><td><b>${d.quota_total}</b></td></tr>
								<tr><td>Used</td><td><b>${d.quota_used}</b></td></tr>
								<tr><td>Remaining</td><td><b>${d.quota_remain}</b></td></tr>
								<tr><td>Used Today</td><td><b>${d.today_used}</b></td></tr>
								<tr><td>Max Daily</td><td><b>${d.max_track_daily}</b></td></tr>
								<tr><td>Free Email Quota</td><td><b>${d.free_email_quota}</b></td></tr>
								<tr><td>Free Email Used</td><td><b>${d.free_email_quotaused}</b></td></tr>
							</table>
						`,
					})
				},
			})
		})

		if (!frm.doc.api_key) {
			btn.prop('disabled', true)
		}
	},
})