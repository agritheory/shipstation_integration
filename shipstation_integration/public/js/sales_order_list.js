frappe.listview_settings['Sales Order'] = {
	onload: listview => {
		listview.__proto__.get_tags_html = get_tags_html
	},
}

const get_tag_color = tag => {
	const tag_entry = frappe.boot.tags.find(t => t.name === tag)
	return tag_entry ? tag_entry.color : null
}

const get_tags_html = (user_tags, limit) => {
	if (!user_tags || user_tags.trim() === ',') {
		return ''
	}
	const tags_array = user_tags.split(',').slice(1, limit + 1)

	if (tags_array.length === 0) {
		return ''
	}

	const tag_htmls = tags_array.map(tag => {
		const color = get_tag_color(tag)
		let style = ''

		if (color) {
			style = `background-color: ${color}; color: ${contrast(color)}`
		} else {
			const palette = frappe.get_palette(tag)
			style = `background-color: var(${palette[0]}); color: var(${palette[1]})`
		}

		return `<div class="tag-pill ellipsis" title="${tag}" style="${style}">${tag}</div>`
	})

	return tag_htmls.join('')
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
