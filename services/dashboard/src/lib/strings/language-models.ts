/** Model per reply language (Model & keys tab, engine page). */
export const languageModelsStrings = {
	title: "Model per language",
	sub: "Answer English and Hindi questions with the model that suits each",
	lead: "The assistant notices the language of each message. Hindi and Hinglish (Hindi typed in English letters) can be answered by a model chosen for them; every other message uses the main model above.",
	mode: "Language switch",
	modeHint:
		"Switching only changes the model that writes the answer. Searching, labelling and follow-up ideas keep using the fast model.",
	modes: {
		default: "Use the engine's setting",
		custom: "Choose a model for each language",
		off: "Off: every language uses the main model",
	} as Record<string, string>,
	engineSetting: "Engine setting: {v}",
	engineNone: "no language switch",
	rows: {
		en: { label: "English", hint: "Messages in English." },
		hi: { label: "Hindi", hint: "Messages written in Devanagari." },
		"hi-Latn": { label: "Hinglish", hint: "Hindi written in English letters, like “ghar pe sample kab aayega”." },
	} as Record<string, { label: string; hint: string }>,
	sameAsMain: "Same as main model",
	models: {
		"google/gemma-4-31b-it": "Gemma 4 31B: best Hindi tone, fastest first word",
		"openai/gpt-oss-120b": "gpt-oss-120b: best English answers",
		"openai/gpt-oss-20b": "gpt-oss-20b: cheapest",
	} as Record<string, string>,
	whyBench: "Why these two? They scored best in our answer benchmark.",
	thinkingNote: "These models answer at their lowest thinking setting, which keeps the first word quick.",
	needsOwner: "Only a workspace owner can change the models.",
	engine: {
		label: "Answer model per language",
		none: "None: every language uses the answer model",
		row: "{lang}: {model}",
	},
};
