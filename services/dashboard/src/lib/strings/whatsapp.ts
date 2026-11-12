/**
 * WhatsApp (TV6). Plain words for a business: "number", "chat", "customer", "reply", "balance"
 * (never "WABA", "webhook", "thread", "tenant", "corpus"). The deployer's side (connecting by
 * hand, fee rates, the plan switch) says what Meta is and who pays.
 */
export const whatsappStrings = {
	nav: "WhatsApp",
	title: "WhatsApp",
	lead: "Let your customers ask your assistant on WhatsApp, on your own number. It answers from the same knowledge as your website, and you can step in at any time.",
	loadError: "We couldn't load your WhatsApp settings.",
	noKbTitle: "Create a knowledge base first",
	noKbBody: "WhatsApp answers from a knowledge base. Create one, then come back to connect your number.",
	noKbAction: "Create a knowledge base",
	notAvailableTitle: "WhatsApp is not part of your plan",
	notAvailableBody: "Ask {product} to add WhatsApp to your plan, and this page will let you connect your number.",
	notSetUpTitle: "WhatsApp is not set up here yet",
	// OS3
	publicAddressMissing:
		"WhatsApp needs a public https address. Set RAG_PUBLIC_URL to the address of this server and restart.",
	// /OS3
	notSetUpBody: "{product} has not finished setting WhatsApp up. Please ask them to enable it.",

	connect: {
		title: "Connect your WhatsApp number",
		body: "Sign in with Facebook and pick the number your customers already know. You pay Meta for the messages yourself, from your own WhatsApp Business account.",
		button: "Connect with Meta",
		working: "Connecting…",
		opening: "Opening Meta's window…",
		coexistence: "I also use this number in the WhatsApp Business app",
		coexistenceHint:
			"Tick this if you want to keep answering from the app too. Your assistant then steps back whenever you reply.",
		success: "Your WhatsApp number is connected.",
		cancelled: "The sign-in was cancelled. Nothing was changed.",
		incomplete:
			"Meta finished without giving us a phone number to connect. Please try again and add or choose a phone number in Meta's window.",
		sdkFailed: "We couldn't open Meta's window. Check that nothing is blocking facebook.com, then try again.",
		noEmbedded: "Connecting from this page isn't switched on yet. Ask {product} to connect your number for you.",
		ownerOnly: "Only owners can connect a number.",
		failedStep: {
			exchange_code: "Meta didn't accept the sign-in. Please try again.",
			register: "Meta couldn't switch the number on for the assistant. Please try again, or ask {product} for help.",
			subscribe: "We couldn't ask Meta to send us your customers' messages. Please try again.",
			read_number: "We couldn't read the number back from Meta. Please try again.",
		} as Record<string, string>,
	},

	number: {
		title: "Your number",
		status: {
			active: "Connected",
			pending: "Waiting for Meta",
			error: "Needs attention",
			disabled: "Switched off",
		} as Record<string, string>,
		statusHelp: {
			error: "Meta didn't accept our last request. Check the connection, or connect the number again.",
			disabled: "Your assistant is not answering on this number. Switch it on in the settings.",
		} as Record<string, string>,
		name: "Name on WhatsApp",
		setup: { embedded: "Connected by you", manual: "Connected by {product}" },
		billing: {
			business: "You pay Meta for the messages, on your own WhatsApp Business account.",
			deployer: "Meta's message fees are handled by {product}.",
		},
		coexistence: "Also used in the WhatsApp Business app",
		check: "Check the connection",
		checking: "Checking…",
		checkOk: "Meta can reach your number.",
		checkQuality: "Quality rating: {rating}",
		checkFailed: "Meta didn't accept the connection ({code}).",
		disconnect: "Disconnect",
		disconnectTitle: "Disconnect this number?",
		disconnectBody:
			"Your assistant stops answering on WhatsApp and the connection to Meta is removed. Your chats stay here, and you can connect a number again later.",
		disconnectConfirm: "Disconnect",
		disconnected: "Your WhatsApp number is disconnected.",
	},

	settings: {
		title: "How it works",
		enabled: "Answer on WhatsApp",
		enabledHelp: "Switch off to pause your assistant on this number without disconnecting it.",
		pause: "After you reply, stay quiet for",
		pauseHint:
			"When you answer a customer yourself, the assistant lets you carry on and does not answer that chat for this long.",
		hours: "hours",
		saved: "Settings saved.",
		followUp: {
			title: "Follow up with people who asked for a call",
			help: "Off by default. If a customer asked for a person and said yes to being messaged here, and nobody replied, we send one message from a template you wrote and Meta approved.",
			warning:
				"Meta must have approved the template first, and it must not contain any {{variables}}. A message that is not approved is never sent.",
			enabled: "Send one follow-up",
			template: "Template name",
			templateHint: "Exactly as it appears in your WhatsApp Manager, for example callback_followup.",
			language: "Template language",
			languageHint: "For example en or en_US.",
			after: "Send it after",
			incomplete: "Add the template name and language to switch this on.",
		},
		stopNote: "A customer who writes STOP is never messaged first again. A later question from them is still answered.",
	},

	documents: {
		title: "Files your assistant can send",
		help: "When a customer asks for a brochure, price list or menu, your assistant sends the matching file from this list. Only the files you tick here are ever sent.",
		empty: "Add a PDF, JPG or PNG to your knowledge first, then come back to choose.",
		limit: "You can choose up to 20 files.",
		saved: "Your choice is saved.",
		chosen: "{n} chosen",
		loadError: "We couldn't load your files.",
		notSendable: "Only PDF, JPG and PNG files can be sent.",
	},

	chats: {
		title: "Chats",
		empty: "No customer has written on WhatsApp yet.",
		emptyUnread: "No unread chats.",
		unreadOnly: "Unread only",
		unread: "{n} unread",
		paused: "You're handling this",
		pausedUntil: "Assistant quiet until {when}",
		stopped: "Asked not to be messaged",
		windowClosed: "More than 24 hours since they wrote",
		open: "Open chat with {name}",
		loadError: "We couldn't load your chats.",
		selectOne: "Pick a chat to read it and reply.",
		lastWrote: "Last wrote {when}",
	},

	chat: {
		back: "All chats",
		loadError: "We couldn't open this chat.",
		who: { user: "Customer", assistant: "Assistant", staff: "You" } as Record<string, string>,
		pausedBanner: "The assistant is staying quiet in this chat{until}.",
		resume: "Let the assistant answer again",
		resumed: "The assistant will answer this chat again.",
		pauseNow: "Keep the assistant quiet",
		pausedNow: "The assistant will stay quiet in this chat.",
		number: "Number: {number}",
		empty: "No messages yet.",
	},

	reply: {
		label: "Your reply",
		placeholder: "Write to the customer…",
		send: "Send",
		sending: "Sending…",
		sent: "Sent. The assistant stays quiet in this chat for a while.",
		count: "{n} of 4,096",
		tooLong: "A reply can have up to 4,096 characters.",
		closedTitle: "You can't reply here any more",
		closedBody:
			"WhatsApp only lets you write freely within 24 hours of the customer's last message. When they write again, you can reply.",
		windowClosed: "More than 24 hours have passed since this customer wrote.",
		failed: "WhatsApp didn't accept the message. Please try again.",
	},

	admin: {
		connectTitle: "WhatsApp number",
		connectBody: "Connect a number from your own Meta app for this business.",
		connectButton: "Connect WhatsApp for this business",
		replaceButton: "Replace the connection",
		dialogTitle: "Connect a WhatsApp number by hand",
		dialogLead:
			"Use this for a number on your own WhatsApp Business account. Paste a permanent token from your Meta app: it is checked, then stored encrypted and never shown again.",
		phoneNumberId: "Phone number ID",
		phoneNumberIdHint: "Digits only, from WhatsApp Manager.",
		wabaId: "WhatsApp Business Account ID",
		wabaIdHint: "Digits only.",
		token: "Permanent access token",
		tokenHint: "Stored encrypted. Only its last four characters are shown afterwards.",
		pin: "Two-step verification PIN (optional)",
		pinHint:
			"Six digits. Give one and the engine registers the number with the WhatsApp Cloud API for you. Leave it empty if you already registered the number in Meta's dashboard.",
		pinError: "Exactly six digits, or leave it empty.",
		registerFailed:
			"Meta didn't register the number. If it already has a PIN, enter that one; otherwise register it in Meta's dashboard and leave the PIN empty.",
		billing: "Who pays Meta for the messages",
		billingDeployer: "I do (and I pass the fees on in the plan)",
		billingBusiness: "The business, from its own WhatsApp Business account",
		submit: "Connect",
		submitting: "Connecting…",
		done: "WhatsApp is connected for this business.",
		idError: "Digits only, 5 to 20 of them.",
		tokenError: "Paste the token from your Meta app.",
		tokenRejected: "Meta didn't accept this token for that number.",
		inUse: "That number is already connected to another knowledge base.",
		notInPlan: "This business's plan does not include WhatsApp. Switch it on in the plan first.",
		notEnabled: "WhatsApp isn't set up on this engine: add the Meta app settings first.",
	},

	rates: {
		title: "WhatsApp message fees",
		lead: "Meta charges for some WhatsApp messages. If a business uses your WhatsApp account and its plan passes the fees on, each message adds a charge at the rates you type here. Look up Meta's current price list for your country; nothing is built in, and every rate starts at zero.",
		currency: "{currency} rates, per message",
		service: "Service reply",
		utility: "Utility template",
		marketing: "Marketing template",
		authentication: "Authentication template",
		free: "Free service replies per number, per month",
		freeHint: "Meta gives some customer-started conversations free. Enter how many to leave uncharged.",
		unit: "In {minor}, for example 35 means {example}.",
		save: "Save rates",
		saved: "Fee rates saved.",
	},

	plan: {
		included: "WhatsApp included",
		includedHelp: "The business can connect a WhatsApp number and answer on it.",
		fees: "Meta's message fees",
		feesHelp: "For a number on your own WhatsApp account.",
		passThrough: "Pass on to the business",
		feesIncluded: "Included in the plan",
	},

	badge: { whatsapp: "WhatsApp", widget: "Website", api: "API", conversation: "Chat", stream: "API" } as Record<
		string,
		string
	>,
};
