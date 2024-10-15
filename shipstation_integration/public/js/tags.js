frappe.ui.Tags.prototype.get_tag = function (label) {
	let colored = true
	let $tag = frappe.get_data_pill(
		label,
		label,
		(target, pill_wrapper) => {
			this.removeTag(target)
			pill_wrapper.closest('.form-tag-row').remove()
		},
		null,
		colored
	)

	frappe.db.get_value('Tag', label, 'color', data => {
		let color = data.color
		if (color) {
			$tag.css({ 'background-color': color })
			$tag.find('.pill-label').css('color', contrast(color))
		}
	})

	if (this.onTagClick) {
		$tag.on('click', '.pill-label', () => {
			this.onTagClick(label)
		})
	}
	return $tag
}

function contrast(color) {
	if (
		(['E', 'F'].includes(color.substring(1, 2).toUpperCase()) &&
			['E', 'F'].includes(color.substring(3, 4).toUpperCase())) ||
		(['E', 'F'].includes(color.substring(3, 4).toUpperCase()) &&
			['E', 'F'].includes(color.substring(5, 6).toUpperCase())) ||
		(['E', 'F'].includes(color.substring(1, 2).toUpperCase()) &&
			['E', 'F'].includes(color.substring(5, 6).toUpperCase()))
	) {
		return '#192734'
	} else {
		return '#FFFFFF'
	}
}
