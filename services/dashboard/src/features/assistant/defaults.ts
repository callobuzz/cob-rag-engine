import type { LangKey } from "./schemas";

/** Languages the tabs offer first; others appear when the assistant supports them. */
export const CORE_LANGS: LangKey[] = ["en", "hi", "hi-Latn"];

/** The supported languages in tab order (English first), at least English. */
export function tabLangs(supported: readonly LangKey[] | undefined): LangKey[] {
	const set = new Set<LangKey>(["en", ...(supported ?? CORE_LANGS)]);
	return [...set];
}

/** A unique slug id (`^[a-z0-9][a-z0-9_-]*$`, up to 40) for a new topic, rule or field. */
export function slugFor(name: string, taken: readonly string[], fallback = "item"): string {
	const base =
		name
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-+|-+$/g, "")
			.slice(0, 34) || fallback;
	let id = base;
	for (let n = 2; taken.includes(id); n++) id = `${base}-${n}`;
	return id;
}

/** Sample disclaimers per sensitive field. Plain wording; the owner should check it with an advisor. */
export const PROFILE_DISCLAIMERS: Record<string, Partial<Record<LangKey, string>>> = {
	medical: {
		en: "This is general information, not medical advice. For symptoms or emergencies, contact a doctor or call 112.",
		hi: "यह सामान्य जानकारी है, चिकित्सा सलाह नहीं। लक्षणों या आपात स्थिति में डॉक्टर से मिलें या 112 पर कॉल करें।",
		"hi-Latn":
			"Yeh general jaankari hai, medical salah nahi. Symptoms ya emergency mein doctor se milein ya 112 par call karein.",
	},
	legal: {
		en: "This is general information, not legal advice. For your situation, speak to a qualified lawyer.",
		hi: "यह सामान्य जानकारी है, कानूनी सलाह नहीं। अपनी स्थिति के लिए किसी योग्य वकील से बात करें।",
		"hi-Latn":
			"Yeh general jaankari hai, legal salah nahi. Apni situation ke liye kisi qualified vakeel se baat karein.",
	},
	financial: {
		en: "This is general information, not financial advice. Check the details with an authorised advisor before you decide.",
		hi: "यह सामान्य जानकारी है, वित्तीय सलाह नहीं। निर्णय से पहले किसी अधिकृत सलाहकार से जाँच लें।",
		"hi-Latn":
			"Yeh general jaankari hai, financial salah nahi. Faisla karne se pehle kisi authorised advisor se check kar lein.",
	},
	education: {
		en: "Fees, dates and eligibility can change. Please confirm with our office before you apply.",
		hi: "फीस, तारीखें और पात्रता बदल सकती हैं। आवेदन से पहले हमारे कार्यालय से पुष्टि कर लें।",
		"hi-Latn": "Fees, dates aur eligibility badal sakti hain. Apply karne se pehle hamare office se confirm kar lein.",
	},
	government: {
		en: "This is guidance, not an official decision. Rules change; confirm with the department's official notice.",
		hi: "यह मार्गदर्शन है, आधिकारिक निर्णय नहीं। नियम बदलते हैं; विभाग की आधिकारिक सूचना से पुष्टि करें।",
		"hi-Latn":
			"Yeh margdarshan hai, official faisla nahi. Niyam badalte hain; vibhag ki official soochna se confirm karein.",
	},
};

/** True when `text` is empty or one of the sample disclaimers (so a profile change may replace it). */
export function isSampleDisclaimer(text: Partial<Record<LangKey, string>>): boolean {
	const values = Object.values(text).filter((v) => (v ?? "").trim() !== "");
	if (values.length === 0) return true;
	return Object.values(PROFILE_DISCLAIMERS).some((p) => values.every((v) => Object.values(p).includes(v)));
}

/** "{assistant_name}" and "{business_name}" filled the way the engine does (only those two). */
export function fillPersona(text: string, vars: { assistant_name: string; business_name: string }): string {
	return text
		.replaceAll("{assistant_name}", vars.assistant_name)
		.replaceAll("{business_name}", vars.business_name || "our business");
}

// TV5 disclosure
/** The engine's rule (`surfaces.disclosure_line`): the owner's wording stands only if it says
 * "AI" (or the Hindi form) as a whole word; otherwise visitors get the standard line. */
const AI_WORD = /(?<![\p{L}\p{N}_])(?:AI|एआई)(?![\p{L}\p{N}_])/iu;
export function saysAi(text: string): boolean {
	return AI_WORD.test(text);
}

export const STANDARD_DISCLOSURE = "I'm {assistant_name}, the AI assistant of {business_name}.";
// /TV5 disclosure
