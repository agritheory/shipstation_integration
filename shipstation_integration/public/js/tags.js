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
			$tag.css('background-color', color)
		}
	})

	if (this.onTagClick) {
		$tag.on('click', '.pill-label', () => {
			this.onTagClick(label)
		})
	}

	return $tag
}
