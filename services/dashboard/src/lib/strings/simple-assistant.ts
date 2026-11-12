/**
 * The simple Assistant page (BIZ3): the business's own bot, named and tuned by the owner
 * ("Shree Dental Assistant"). Plain words; the full settings stay under the knowledge base.
 */
export const simpleAssistantStrings = {
	title: "Assistant",
	lead: "Your assistant is the chat visitors meet on your website. Give it a name and a picture, and tell it how to talk.",
	allSettings: "All settings",
	allSettingsHint: "Topics, rules, leads, versions and more.",
	noKbTitle: "Create a knowledge base first",
	noKbBody: "Your assistant answers from a knowledge base. Create one and your assistant will be set up with it.",
	noKbAction: "Create a knowledge base",
	loadError: "We couldn't load your assistant.",
	sections: {
		identity: {
			title: "Name and business details",
			help: "The name visitors see, your picture, and what your business does. Example: Shree Dental Assistant for Shree Dental Clinic, Pune.",
		},
		voice: {
			title: "How it talks",
			help: "Friendly, formal or short and direct. This sets the tone of every answer.",
		},
		language: {
			title: "Languages",
			help: "The languages your assistant answers in, including Hinglish.",
		},
		handoff: {
			title: "When your assistant isn't sure",
			help: "Where it sends people when it cannot help, such as your WhatsApp number or a call back.",
		},
	},
} as const;
