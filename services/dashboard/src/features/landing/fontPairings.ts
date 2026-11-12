/**
 * The six font pairings the website can use, in the words an owner reads. The engine holds the
 * real faces (`landing/fonts.py`, self-hosted); `tests/unit/test_landing_editor_fonts.py` fails
 * when this list and the engine's drift apart. The dashboard does not load these fonts: the
 * preview shows them as they will look.
 */
export const FONT_PAIRINGS = [
	{ key: "neutral", name: "Neutral", feel: "Clean and neutral", families: "Inter Tight and Inter" },
	{ key: "engineered", name: "Engineered", feel: "Technical and precise", families: "Geist and Geist Mono" },
	{ key: "editorial", name: "Editorial", feel: "Calm and editorial", families: "Instrument Serif and Inter" },
	{ key: "warm", name: "Warm", feel: "Warm and human", families: "Fraunces and Manrope" },
	{ key: "bold", name: "Bold", feel: "Bold and friendly", families: "Bricolage Grotesque and DM Sans" },
	{ key: "house", name: "House", feel: "Confident and modern", families: "Schibsted Grotesk and Red Hat Text" },
] as const;

export type PairingKey = (typeof FONT_PAIRINGS)[number]["key"];
