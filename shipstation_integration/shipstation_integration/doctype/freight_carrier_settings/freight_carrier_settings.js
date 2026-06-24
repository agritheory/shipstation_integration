// Copyright (c) 2026, AgriTheory and contributors
// For license information, please see license.txt

let ltlProviderPresetsPromise = null

function loadLtlProviderPresets() {
	if (!ltlProviderPresetsPromise) {
		ltlProviderPresetsPromise = frappe
			.xcall(
				'shipstation_integration.shipstation_integration.doctype.freight_carrier_settings.freight_carrier_settings.get_ltl_provider_presets'
			)
			.catch(() => ({}))
	}
	return ltlProviderPresetsPromise
}

function renderProviderPresetButtons(frm, presets) {
	const $host = frm.fields_dict.provider_preset_buttons?.$wrapper
	if (!$host) {
		return
	}

	const keys = Object.keys(presets || {})
	if (!keys.length) {
		$host.html('')
		return
	}

	const buttons = keys
		.map(key => {
			const preset = presets[key]
			const label = frappe.utils.escape_html(preset.label || key)
			return `<button type="button" class="btn btn-default btn-sm" data-preset-key="${frappe.utils.escape_html(
				key
			)}">${label}</button>`
		})
		.join('')

	$host.html(`
		<div class="freight-carrier-preset-toolbar" style="margin-bottom: 6px;">
			<div class="btn-group" role="group" aria-label="${__('LTL provider templates')}">
				${buttons}
			</div>
		</div>
		<p class="help-box small text-muted" style="margin-top: 0;">
			${__(
				'Fills Base URL and provider-specific auth fields. Enter API keys, client credentials, and account number after applying a template.'
			)}
		</p>
	`)

	$host.find('button[data-preset-key]').on('click', event => {
		const key = event.currentTarget.getAttribute('data-preset-key')
		applyProviderPreset(frm, presets, key)
	})
}

function applyProviderPreset(frm, presets, presetKey) {
	const preset = presets[presetKey]
	if (!preset) {
		return
	}

	const run = () => {
		const fields = preset.fields || {}
		Object.keys(fields).forEach(fieldname => {
			frm.set_value(fieldname, fields[fieldname])
		})
		frm.refresh_fields(Object.keys(fields))
		frappe.show_alert({
			message: preset.hint || __('Applied {0}.', [preset.label || presetKey]),
			indicator: 'green',
		})
	}

	const nextBaseUrl = (preset.fields || {}).base_url || ''
	if (frm.doc.base_url && frm.doc.base_url !== nextBaseUrl) {
		frappe.confirm(__('Replace the current provider settings with the {0} template?', [preset.label || presetKey]), run)
		return
	}
	run()
}

frappe.ui.form.on('Freight Carrier Settings', {
	refresh(frm) {
		loadLtlProviderPresets().then(presets => renderProviderPresetButtons(frm, presets))
	},
})
