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

function addProviderPresetButtons(frm, presets) {
	const group = __('Provider Template')
	Object.keys(presets || {}).forEach(key => {
		const preset = presets[key]
		frm.add_custom_button(preset.label || key, () => applyProviderPreset(frm, presets, key), group)
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
		loadLtlProviderPresets().then(presets => addProviderPresetButtons(frm, presets))
	},
})
