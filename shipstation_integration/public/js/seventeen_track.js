// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

frappe.ui.form.on('Seventeen Track', {
	refresh(frm) {
		frappe.call({
			method:
				'shipstation_integration.shipstation_integration.doctype.seventeen_track.seventeen_track.get_webhook_callback_uri',
			callback(r) {
				const url = r.message
				if (!url) return
				if (frm.doc.webhook_callback_uri !== url) {
					frm.set_value('webhook_callback_uri', url)
				}
				frm.remove_custom_button(__('Copy Webhook URL'))
				frm.add_custom_button(__('Copy Webhook URL'), function () {
					frappe.utils.copy_to_clipboard(url)
					frappe.show_alert({ message: __('Copied'), indicator: 'green' })
				})
			},
		})

		const btn = frm.add_custom_button(__('Check Quota'), function () {
			frm.call({
				doc: frm.doc,
				method: 'fetch_quota',
				freeze: true,
				callback(r) {
					if (!r.message) return
					const d = r.message
					frappe.msgprint({
						title: __('17Track Quota'),
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

		if (frm.is_new() || !frm.doc.name) {
			btn.prop('disabled', true)
		}
	},

	after_save(frm) {
		if (frm.doc.seventeen_track_user) {
			return
		}
		const can_create_user = frappe.user.has_role('System Manager') || frappe.session.user === 'Administrator'
		if (!can_create_user) {
			return
		}
		frappe.confirm(
			__('Create dedicated user 17Track so 17Track updates (comments and document changes) run as that user?'),
			() => {
				frm.call({
					doc: frm.doc,
					method: 'ensure_integration_user',
					freeze: true,
					callback() {
						frm.reload_doc()
					},
				})
			},
			() => {},
			__('Create 17Track user?')
		)
	},
})
