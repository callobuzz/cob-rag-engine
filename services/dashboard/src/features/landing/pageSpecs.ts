import { type FieldSpec, ICONS } from "./sections";

/**
 * The fields of the pages other than the home page, the menu, the footer and the search texts.
 * Same idea as `sections.ts`: a list the editor draws a form from; the engine checks the result.
 */
const T = (key: string, label: string, extra: Partial<Extract<FieldSpec, { k: "text" }>> = {}): FieldSpec => ({
	k: "text",
	key,
	label,
	...extra,
});
const blank = () => ({ en: "" });

export const PRICING_FIELDS: FieldSpec[] = [
	{ k: "switch", key: "enabled", label: "Show the pricing page" },
	T("headline", "Headline", { required: true }),
	T("sub", "Line under the headline", { long: true }),
	T("tax_line_in", "Tax line for India", { hint: "Like “Prices include GST.”" }),
	T("tax_line_global", "Tax line for other countries"),
	T("credit_box", "What is a credit?", { long: true }),
	{
		k: "list",
		key: "credit_examples",
		label: "Examples of what a credit buys",
		itemLabel: "Example",
		max: 3,
		texts: true,
		make: blank,
	},
	{ k: "switch", key: "show_packs", label: "Show credit packs" },
	{ k: "switch", key: "show_services", label: "Show one-time services" },
	{ k: "switch", key: "show_whatsapp_fees", label: "Explain WhatsApp fees" },
	{ k: "switch", key: "show_spend_cap", label: "Explain the spending limit" },
	{ k: "switch", key: "comparison_table", label: "Show the comparison table" },
	{
		k: "list",
		key: "faq",
		label: "Questions about pricing",
		itemLabel: "Question",
		max: 10,
		make: () => ({ q: blank(), a: blank() }),
		fields: [T("q", "Question"), T("a", "Answer", { long: true })],
	},
];

export const FEATURES_FIELDS: FieldSpec[] = [
	{ k: "switch", key: "enabled", label: "Show the features page" },
	T("headline", "Headline", { required: true }),
	T("sub", "Line under the headline", { long: true }),
	{
		k: "list",
		key: "groups",
		label: "Groups of features",
		itemLabel: "Group",
		max: 6,
		make: () => ({ title: blank(), items: [{ icon: "check", title: blank(), text: blank() }] }),
		fields: [
			T("title", "Group title"),
			{
				k: "list",
				key: "items",
				label: "Features",
				itemLabel: "Feature",
				max: 8,
				min: 1,
				make: () => ({ icon: "check", title: blank(), text: blank() }),
				fields: [
					{ k: "select", key: "icon", label: "Icon", options: ICONS },
					T("title", "Title"),
					T("text", "Text", { long: true }),
				],
			},
		],
	},
	{
		k: "list",
		key: "checklist",
		label: "Quick questions and answers",
		itemLabel: "Item",
		max: 6,
		make: () => ({ q: blank(), a: blank() }),
		fields: [T("q", "Question"), T("a", "Answer", { long: true })],
	},
	T("cta_label", "Button at the bottom"),
];

export const ABOUT_FIELDS: FieldSpec[] = [
	{ k: "switch", key: "enabled", label: "Show the about page" },
	T("headline", "Headline", { required: true }),
	{
		k: "list",
		key: "story",
		label: "Your story, paragraph by paragraph",
		itemLabel: "Paragraph",
		max: 6,
		texts: true,
		make: blank,
	},
	{
		k: "list",
		key: "values",
		label: "What you stand for",
		itemLabel: "Value",
		max: 4,
		make: () => ({ title: blank(), text: blank() }),
		fields: [T("title", "Title"), T("text", "Text", { long: true })],
	},
	{
		k: "list",
		key: "team",
		label: "The team",
		itemLabel: "Person",
		max: 8,
		make: () => ({ name: "", role: blank() }),
		fields: [{ k: "plain", key: "name", label: "Name" }, T("role", "Role")],
	},
	{ k: "switch", key: "show_company_details", label: "Show your company details at the bottom" },
];

/** The contact page's fields; the four topics are drawn by the page itself. */
export const CONTACT_FIELDS: FieldSpec[] = [
	{ k: "switch", key: "enabled", label: "Show the contact page" },
	T("headline", "Headline", { required: true }),
	T("sub", "Line under the headline", { long: true }),
	{
		k: "switch",
		key: "form_enabled",
		label: "Show the contact form",
		hint: "Messages arrive in Support, marked “From your website”.",
	},
	{ k: "switch", key: "show_whatsapp", label: "Show your WhatsApp number" },
	{ k: "switch", key: "show_phone", label: "Show your phone number" },
	{ k: "switch", key: "show_email", label: "Show your e-mail address" },
	{ k: "switch", key: "show_address", label: "Show your address" },
	T("response_time", "How soon you reply"),
	T("consent", "Consent line under the form", { long: true }),
	T("success", "Message after sending", { long: true }),
];

export const TOPICS: { key: "sales" | "support" | "partnership" | "other"; label: string }[] = [
	{ key: "sales", label: "Topic: sales" },
	{ key: "support", label: "Topic: support" },
	{ key: "partnership", label: "Topic: partnership" },
	{ key: "other", label: "Topic: something else" },
];

export const NAV_FIELDS: FieldSpec[] = [
	{ k: "links", key: "links", label: "Menu links", max: 6 },
	{ k: "switch", key: "show_sign_in", label: "Show Sign in" },
	{ k: "switch", key: "show_sign_up", label: "Show Sign up" },
];

export const FOOTER_FIELDS: FieldSpec[] = [
	{
		k: "list",
		key: "columns",
		label: "Footer columns",
		itemLabel: "Column",
		max: 4,
		make: () => ({ title: blank(), links: [] }),
		fields: [T("title", "Column title"), { k: "links", key: "links", label: "Links", max: 6 }],
	},
	{ k: "switch", key: "show_legal", label: "Show Terms, Privacy and the other legal pages" },
	T("note", "Note at the bottom", { long: true }),
];

export const PAGE_SEO: { key: "home" | "pricing" | "features" | "about" | "contact"; label: string }[] = [
	{ key: "home", label: "Home page" },
	{ key: "pricing", label: "Pricing page" },
	{ key: "features", label: "Features page" },
	{ key: "about", label: "About page" },
	{ key: "contact", label: "Contact page" },
];

export const ANNOUNCEMENT_FIELDS: FieldSpec[] = [
	{ k: "switch", key: "enabled", label: "Show a bar at the top of every page" },
	T("text", "Text of the bar"),
	{
		k: "plain",
		key: "href",
		label: "Where the bar links to (optional)",
		hint: "A page of this site, like /pricing, or an https:// address.",
	},
];
