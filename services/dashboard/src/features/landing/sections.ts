/**
 * What each home page section is called and which fields it has (LP4). The editor draws a form
 * from these lists; the engine validates the result. A text's longest length is never written
 * here: it comes from the `limits` the engine sends, by the key `<section type>.<field path>`.
 */
export type Option = { value: string; label: string };

export type FieldSpec =
	| { k: "text"; key: string; label: string; long?: boolean; required?: boolean; hint?: string }
	| { k: "plain"; key: string; label: string; hint?: string; placeholder?: string }
	| { k: "number"; key: string; label: string; hint?: string }
	| { k: "switch"; key: string; label: string; hint?: string }
	| { k: "select"; key: string; label: string; options: Option[] }
	| { k: "multi"; key: string; label: string; options: Option[] }
	| { k: "asset"; key: string; label: string; assetKind: "video" | "poster" }
	| { k: "plans"; key: string; label: string; max: number; hint?: string }
	| { k: "group"; key: string; label: string; fields: FieldSpec[] }
	| {
			k: "list";
			key: string;
			label: string;
			itemLabel: string;
			max: number;
			min?: number;
			/** A list of bare texts (chips, sentences) instead of objects. */
			texts?: boolean;
			fields?: FieldSpec[];
			make: () => unknown;
	  }
	| { k: "links"; key: string; label: string; max: number }
	| { k: "conversation"; key: string; label: string }
	| { k: "lead"; key: string; label: string; optional?: boolean };

export const SECTION_TITLES: Record<string, { title: string; about: string }> = {
	hero: { title: "Top of the page", about: "The headline, the call to action and the sample conversation." },
	trust_strip: { title: "Trust strip", about: "A short line of reasons to trust you." },
	pain_wall: {
		title: "Questions your customers ask late at night",
		about: "Real-sounding questions that arrive when no one is in the office.",
	},
	how_it_works: { title: "How it works", about: "Three simple steps." },
	grounded: {
		title: "Answers only from your content",
		about: "How the assistant stays on facts and says when it does not know.",
	},
	languages: { title: "Replies in their language", about: "Three sample questions in different languages." },
	whatsapp_journey: { title: "A day on WhatsApp", about: "A conversation that moves from the website to WhatsApp." },
	leads: { title: "Leads you collect", about: "A sample lead card, with the consent line the visitor saw." },
	human_handoff: { title: "Handing over to a person", about: "How a visitor reaches you, with a reference code." },
	value_report: { title: "What it saves, estimated", about: "A sample monthly summary. Always shown as an estimate." },
	bento: { title: "What you get", about: "Three to seven short tiles." },
	proof: { title: "Customer stories", about: "Quotes and links. Only add real ones." },
	pricing_teaser: { title: "Pricing preview", about: "A short look at your plans, linking to the pricing page." },
	privacy: { title: "Your data and privacy", about: "Plain sentences about how data is handled." },
	faq: { title: "Questions and answers", about: "Common questions with short answers." },
	video: { title: "Video", about: "A demo video with a poster picture." },
	final_cta: { title: "Last call to action", about: "The closing message and button." },
};

const T = (key: string, label: string, extra: Partial<Extract<FieldSpec, { k: "text" }>> = {}): FieldSpec => ({
	k: "text",
	key,
	label,
	...extra,
});

const EMPTY: { en: string } = { en: "" };
const blankText = () => ({ ...EMPTY });

const LEAD_FIELDS: FieldSpec[] = [
	{ k: "plain", key: "name", label: "Name on the card", placeholder: "Priya S." },
	{
		k: "plain",
		key: "phone_masked",
		label: "Phone number, partly hidden",
		hint: "Hide the middle digits, like +91 98•••• 4512.",
	},
	T("looking_for", "Looking for"),
	{
		k: "select",
		key: "came_from",
		label: "Came from",
		options: [
			{ value: "website", label: "The website" },
			{ value: "whatsapp", label: "WhatsApp" },
		],
	},
	T("consent", "The consent line the visitor saw", { long: true }),
];

const CHANNEL_OPTIONS: Option[] = [
	{ value: "website", label: "Website chat" },
	{ value: "whatsapp", label: "WhatsApp" },
];

export const ICONS: Option[] = [
	"chat",
	"clock",
	"shield",
	"globe",
	"chart",
	"lock",
	"users",
	"bolt",
	"book",
	"phone",
	"mail",
	"check",
	"star",
	"heart",
	"tag",
	"rupee",
].map((value) => ({ value, label: value.charAt(0).toUpperCase() + value.slice(1) }));

/** Fields every section can carry, shown under "Intro texts". */
export const INTRO_FIELDS: FieldSpec[] = [
	T("narrator", "Narrator line"),
	T("eyebrow", "Small label above the heading"),
	T("heading", "Heading"),
	T("body", "Text under the heading", { long: true }),
];

const titleText = (): FieldSpec[] => [T("title", "Title"), T("text", "Text", { long: true })];

export const SECTION_FIELDS: Record<string, FieldSpec[]> = {
	hero: [
		T("headline", "Headline", { required: true }),
		T("sub", "Line under the headline", { long: true }),
		T("primary_label", "Main button"),
		{
			k: "plain",
			key: "primary_href",
			label: "Main button goes to",
			hint: "A page of this site, like /signup, or an https:// address.",
		},
		T("microcopy", "Small print under the button"),
		T("ask_label", "Ask-it-yourself button"),
		T("whatsapp_label", "WhatsApp button"),
		{ k: "switch", key: "show_pricing_link", label: "Show a link to the pricing page" },
		{
			k: "select",
			key: "visual",
			label: "What the sample shows",
			options: [
				{ value: "both", label: "Website and WhatsApp side by side" },
				{ value: "website", label: "Website chat only" },
				{ value: "whatsapp", label: "WhatsApp only" },
			],
		},
		{
			k: "switch",
			key: "backdrop_video",
			label: "Play the video behind the headline",
			hint: "Needs a video section with a poster picture.",
		},
		{ k: "conversation", key: "conversation", label: "Sample conversation" },
	],
	trust_strip: [
		{ k: "list", key: "chips", label: "Short chips", itemLabel: "Chip", max: 8, texts: true, make: blankText },
		T("statement", "One-line statement"),
	],
	pain_wall: [
		{
			k: "list",
			key: "questions",
			label: "Questions",
			itemLabel: "Question",
			max: 12,
			make: () => ({ text: blankText(), channel: "website", time: "23:30" }),
			fields: [
				T("text", "Question"),
				{ k: "select", key: "channel", label: "Asked on", options: CHANNEL_OPTIONS },
				{ k: "plain", key: "time", label: "Time (24 hours)", placeholder: "23:30" },
			],
		},
		T("punchline", "Closing line", { long: true }),
	],
	how_it_works: [
		{
			k: "list",
			key: "steps",
			label: "The three steps",
			itemLabel: "Step",
			max: 3,
			min: 3,
			make: () => ({ title: blankText(), text: blankText() }),
			fields: titleText(),
		},
		T("promise", "Promise under the steps"),
	],
	grounded: [
		{
			k: "list",
			key: "behaviours",
			label: "Three things it does",
			itemLabel: "Behaviour",
			max: 3,
			min: 3,
			make: () => ({ title: blankText(), text: blankText() }),
			fields: titleText(),
		},
		T("fact_check_line", "Fact-check line", { long: true }),
		{
			k: "group",
			key: "caught_example",
			label: "An example it caught",
			fields: [T("guess", "What a model might guess"), T("replaced_by", "What your content says")],
		},
	],
	languages: [
		{
			k: "list",
			key: "samples",
			label: "Three samples",
			itemLabel: "Sample",
			max: 3,
			min: 3,
			make: () => ({ lang: "en", question: "", answer: "" }),
			fields: [
				{
					k: "select",
					key: "lang",
					label: "Language",
					options: [
						{ value: "en", label: "English" },
						{ value: "hi", label: "हिंदी" },
						{ value: "hi-Latn", label: "Hinglish" },
					],
				},
				{ k: "plain", key: "question", label: "Question" },
				{ k: "plain", key: "answer", label: "Answer" },
			],
		},
	],
	whatsapp_journey: [
		{
			k: "list",
			key: "chapters",
			label: "Chapters of the day",
			itemLabel: "Chapter",
			max: 5,
			min: 2,
			make: () => ({
				time: "09:00",
				title: blankText(),
				messages: [{ from: "visitor", text: blankText(), attachment: null }],
			}),
			fields: [
				{ k: "plain", key: "time", label: "Time (24 hours)", placeholder: "09:00" },
				T("title", "Chapter title"),
				{
					k: "list",
					key: "messages",
					label: "Messages",
					itemLabel: "Message",
					max: 4,
					min: 1,
					make: () => ({ from: "visitor", text: blankText(), attachment: null }),
					fields: [
						{
							k: "select",
							key: "from",
							label: "Who says it",
							options: [
								{ value: "visitor", label: "The visitor" },
								{ value: "assistant", label: "The assistant" },
								{ value: "owner", label: "The owner" },
							],
						},
						T("text", "Message"),
						T("attachment", "Attached file name (optional)"),
					],
				},
			],
		},
	],
	leads: [
		{ k: "lead", key: "lead", label: "Lead card" },
		{ k: "number", key: "inbox_count", label: "Leads shown waiting in the inbox", hint: "Only decoration: 0 to 99." },
	],
	human_handoff: [
		{
			k: "plain",
			key: "ref_code",
			label: "Example reference code",
			hint: "Two letters, a dash, four letters or digits, like KM-4Q7P.",
		},
		T("prefill", "Message the visitor sends", { long: true }),
		{
			k: "multi",
			key: "channels",
			label: "Ways to reach a person",
			options: [
				{ value: "whatsapp", label: "WhatsApp" },
				{ value: "call", label: "Phone call" },
				{ value: "email", label: "E-mail" },
			],
		},
	],
	value_report: [
		{ k: "number", key: "questions", label: "Questions answered in the sample month" },
		{ k: "number", key: "after_hours", label: "Of them after office hours" },
		{ k: "number", key: "leads", label: "Leads collected" },
		T("value_text", "Estimated value", { hint: "Typed by you, like ₹46,000." }),
		T("caption", "Caption", { long: true, hint: "Must say the value is estimated." }),
	],
	bento: [
		{
			k: "list",
			key: "tiles",
			label: "Tiles",
			itemLabel: "Tile",
			max: 7,
			min: 3,
			make: () => ({ icon: "check", title: blankText(), text: blankText() }),
			fields: [{ k: "select", key: "icon", label: "Icon", options: ICONS }, ...titleText()],
		},
	],
	proof: [
		{
			k: "list",
			key: "cases",
			label: "Customer stories",
			itemLabel: "Story",
			max: 3,
			make: () => ({ quote: blankText(), number: "", method: blankText(), name: "" }),
			fields: [
				T("quote", "What they said", { long: true }),
				{ k: "plain", key: "number", label: "The number they measured", placeholder: "31%" },
				T("method", "How it was measured"),
				{ k: "plain", key: "name", label: "Who said it" },
			],
		},
		{
			k: "list",
			key: "gallery",
			label: "Links to more",
			itemLabel: "Link",
			max: 3,
			make: () => ({ label: blankText(), href: "https://" }),
			fields: [T("label", "Link text"), { k: "plain", key: "href", label: "Address", placeholder: "https://" }],
		},
	],
	pricing_teaser: [
		{
			k: "plans",
			key: "plan_ids",
			label: "Plans to show",
			max: 4,
			hint: "Leave empty to show the same plans as the pricing page.",
		},
		T("note", "Note under the plans"),
	],
	privacy: [
		{ k: "list", key: "sentences", label: "Sentences", itemLabel: "Sentence", max: 8, texts: true, make: blankText },
	],
	faq: [
		{
			k: "list",
			key: "items",
			label: "Questions",
			itemLabel: "Question",
			max: 12,
			make: () => ({ q: blankText(), a: blankText() }),
			fields: [T("q", "Question"), T("a", "Answer", { long: true })],
		},
		{ k: "switch", key: "structured_data", label: "Let search engines read these as questions and answers" },
	],
	video: [
		{ k: "asset", key: "video_asset_id", label: "Video", assetKind: "video" },
		{ k: "asset", key: "poster_asset_id", label: "Poster picture", assetKind: "poster" },
		T("caption", "Caption"),
		{
			k: "select",
			key: "placement",
			label: "Where it plays",
			options: [
				{ value: "section", label: "In its own section" },
				{ value: "hero_backdrop", label: "Behind the headline" },
			],
		},
	],
	final_cta: [
		T("bubble", "Message in the chat bubble", { long: true }),
		T("button_label", "Button"),
		T("sub", "Line under the button"),
		T("whatsapp_label", "WhatsApp button (optional)"),
	],
};

export const LEAD_SPEC: FieldSpec[] = LEAD_FIELDS;

/** The longest a visitor's line in the sample conversation may be (the engine enforces it too). */
export const VISITOR_LINE_MAX = 140;
