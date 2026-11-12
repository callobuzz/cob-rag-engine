/**
 * The business Home (BIZ3). Plain words for a business owner: "your assistant", "your website",
 * "knowledge base" (never "corpus"). `{product}` is the deployer's product name from branding.
 */
export const homeStrings = {
	title: "Home",
	greeting: { morning: "Good morning, {name}", afternoon: "Good afternoon, {name}", evening: "Good evening, {name}" },
	lead: "Here is how your assistant is doing today.",
	loadError: "We couldn't load your Home page.",
	noKb: {
		title: "Set up your first assistant",
		body: "Give {product} your website and it will learn your business, so your assistant can answer your visitors.",
		action: "Create a knowledge base",
	},
	status: {
		title: "Your assistant",
		live: "{name} is live",
		off: "{name} is switched off",
		notPublished: "Your assistant is not published yet",
		notPublishedHint: "Finish its details, then publish it so visitors can chat.",
		unnamed: "Your assistant",
		openAssistant: "Open the Assistant page",
		publishNow: "Set it up",
		forBusiness: "for {business}",
		website: {
			none: "No website connected yet",
			noneHint: "Add your website so your assistant can learn from it.",
			learning: "Reading your website now",
			learned: "{pages} pages learned",
			learnedOne: "1 page learned",
			updated: "last updated {when}",
			failed: "We couldn't read your website",
			paused: "Website reading is paused",
			off: "Website learning is not included in your plan",
			add: "Add your website",
			open: "Open Knowledge",
		},
	},
	today: {
		title: "Today",
		conversations: "People who chatted",
		leads: "Contact details left",
		waiting: "Waiting for a call back",
		waitingHint: "People who asked for a person",
		seeChats: "See conversations",
		seeLeads: "See contact details",
		seeWaiting: "See who is waiting",
		date: "{date}",
	},
	plan: {
		title: "Your plan",
		seeMore: "Plan & billing",
		noData: "Your plan details will show here.",
	},
	actions: {
		title: "Quick actions",
		copyCode: "Copy your website code",
		copyCodeHint: "Paste it once on your website to show the assistant.",
		copied: "Copied. Paste it just before the closing body tag of your website.",
		copyFailed: "We couldn't copy it. Open the Website widget page and select the code there.",
		noCode: "Get your website code",
		write: "Write an article",
		writeHint: "Add something your assistant should know, like your fees or timings.",
		tryIt: "Try your assistant",
		tryItHint: "Ask it a question the way a visitor would.",
	},
	tips: {
		title: "Tips from COB",
		openCob: "Open in COB",
		openFor: "Open “{title}” in COB",
		items: [
			{
				title: "Add your most asked questions",
				body: "Write the answers once, such as root canal fees or Sunday timings, and your assistant will use them word for word.",
				starter: "What are the best questions and answers to add for my business?",
			},
			{
				title: "Check what visitors asked",
				body: "Questions your assistant could not answer well are the easiest way to find what is missing.",
				starter: "Which questions did my assistant fail to answer lately?",
			},
			{
				title: "Make it sound like you",
				body: "Choose a friendly or formal tone, and a name your patients or customers will remember.",
				starter: "How do I change my assistant's name and tone?",
			},
		],
	},
	// TV5: next steps
	nudges: {
		title: "Worth doing next",
		dismiss: "Hide “{title}”",
		add_brochures: {
			title: "Add your brochures and price lists",
			body: "Your assistant has no files yet and has read only a few pages. A price list or menu as a PDF helps it answer what customers ask most.",
			action: "Add files",
		},
		no_human_path: {
			title: "Visitors who ask for a person can't reach you",
			body: "Add a phone number, WhatsApp number or email to your business card, or turn on the call-back form, so nobody is left without an answer.",
			action: "Open your assistant",
		},
		set_values: {
			title: "Set what a lead is worth to you",
			body: "You have had plenty of conversations this month. Tell us what a new customer is worth and the value report will use your numbers.",
			action: "Set my values",
		},
	},
	// /TV5
} as const;
