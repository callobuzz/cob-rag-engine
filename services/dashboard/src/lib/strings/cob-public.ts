/**
 * "Ask COB" on the sign-in pages (COB6): the docs-only COB for people who are not signed in.
 * Words: engine, workspace, "COB" always upper-case. The chat is not saved anywhere.
 */
export const cobPublicStrings = {
	launcher: "Ask COB",
	panel: "COB, ask about the engine",
	description: "Ask COB about the engine. Nothing you type here is saved.",
	greeting: "Hi, I'm COB. Ask me about the engine, or about signing in or signing up. I can't see any account.",
	startersLabel: "Questions to start with",
	starters: ["What is cob-rag-engine?", "Can I run it on my own server?", "How do I sign up?"],
	close: "Close COB",
	newChat: "New chat",
	notSaved: "This chat isn't saved. Close it and it's gone.",
	tooLong: "Please keep your question under {max} characters.",
	retry: "Try again",
	generic: "COB couldn't answer just now.",
	rateLimited: "You've asked a lot of questions. Try again in {t}.",
	syncing: "COB is reading the docs for the first time. Try again in a minute.",
	answering: "COB is answering…",
	answered: "COB answered: {text}",
	you: "You",
	cob: "COB",
	sources: { label: "Sources", open: "Open {title}", openPublic: "Open {title} (new tab)" },
	switch: {
		label: "Ask COB on the sign-in pages",
		help: "Visitors can ask COB about the engine from the public pages. Answers use your model key and are limited per visitor and per day.",
		savedOn: "Ask COB is on for the sign-in pages",
		savedOff: "Ask COB is off for the sign-in pages",
	},
	search: {
		banner: "COB can't write answers right now, so here are the matching help pages instead.",
		empty: "No help page matches that.",
		open: "Open",
	},
} as const;
