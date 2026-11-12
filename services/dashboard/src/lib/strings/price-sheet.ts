/**
 * The provider's price list (TV3): which plans it lists, add-ons, a note, a preview, print, and the
 * public link. Words: "price list", "plan", "add-on" (never "tenant", "ledger"). The provider's
 * own product name appears in the page the visitor sees, not here.
 */
export const priceSheetStrings = {
	nav: {
		label: "Price list",
		summary: "A price list of your plans and add-ons to share with prospects: print it, or send a link.",
		items: ["Pick the plans and their order", "Print or save as PDF", "A link you can turn off any time"],
	},
	title: "Price list",
	lead: "A white-label page of your plans and add-ons, with your name, logo and colour. Share the link or print it as a PDF.",
	loadError: "We couldn't load the price list.",
	plans: {
		title: "Plans on the list",
		lead: "Tick the plans to show and put them in order. With none ticked, every active priced plan is shown by price.",
		moveUp: "Move {name} up",
		moveDown: "Move {name} down",
		show: "Show {name}",
		internal: "Not available",
		empty: "No plans yet.",
	},
	options: {
		addOns: "Show add-ons",
		note: "Note under the price list",
		noteHint: "For example: Prices exclude GST. Up to 500 characters.",
		noteLong: "Keep the note under 500 characters.",
		save: "Save",
		saving: "Saving…",
		saved: "Price list saved",
	},
	preview: {
		title: "Preview",
		print: "Print or save as PDF",
		printFailed: "Could not open the printable page.",
		blocked: "Your browser blocked the new tab. Allow pop-ups for this page and try again.",
		frame: "Price list preview",
	},
	link: {
		title: "Public link",
		lead: "Anyone with the link can read the price list. Making a new link stops the old one at once.",
		none: "There is no public link.",
		create: "Create a link",
		rotate: "Make a new link",
		remove: "Remove the link",
		copy: "Copy link",
		copied: "Link copied",
		created: "New link ready",
		removed: "Link removed",
		showOnce: "This is the link now. Copy it and share it.",
		confirmRotateTitle: "Make a new link?",
		confirmRotateBody: "The old link will stop working straight away.",
		confirmRemoveTitle: "Remove the link?",
		confirmRemoveBody: "Anyone using the link will see a not-found page.",
		label: "Public price list link",
	},
} as const;
