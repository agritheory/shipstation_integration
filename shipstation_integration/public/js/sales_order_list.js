frappe.listview_settings['Sales Order'] = {
	onload: listview => {
		listview.__proto__.get_tags_html = get_tags_html
	},
}

const get_tags_html = (user_tags, limit) => {
	user_tags = user_tags.split(',').slice(1, limit + 1)
	const promises = user_tags.map(async tag => {
		await frappe.db.get_value('Tag', tag, 'color').then(data => {
			let style = ''
			let color = data.color
			if (color) {
				style = `background-color: ${color}; color: ${contrast(color)}`
			} else {
				const palette = frappe.get_palette(tag)
				style = `background-color: var(${palette[0]}); color: var(${palette[1]})`
			}
			return `<div class="tag-pill ellipsis" title="${tag}" style="${style}">${tag}</div>`
		})
	})
	Promise.all(promises)
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
