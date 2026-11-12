import type { LandingContent, LandingLang } from "./api";
import { SECTION_TITLES } from "./sections";

/**
 * Pure helpers over the website draft (LP4): where a text lives, which texts a language still
 * lacks, and the section moves and merges the editor makes. The server is the judge of what is
 * valid; nothing here repeats its rules except the two that shape the page: the first section
 * stays first and the last stays last.
 */
export type LText = Record<string, string>;
export type Section = LandingContent["home"][number];
export type TabKey = "home" | "look" | "pricing" | "pages" | "menu" | "languages" | "versions";

export const LANGS: readonly LandingLang[] = ["en", "hi", "hi-Latn"];
export const FIRST_SECTION = "hero";
export const LAST_SECTION = "final_cta";

export function isLText(value: unknown): value is LText {
	if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
	const entries = Object.entries(value);
	return (
		entries.length > 0 && entries.every(([k, v]) => (LANGS as readonly string[]).includes(k) && typeof v === "string")
	);
}

/** The DOM id of a field, from the same path the engine reports for a missing text. */
export function fieldId(path: string): string {
	return `lf-${path.replace(/[^a-zA-Z0-9]+/g, "-").replace(/-$/, "")}`;
}

/** Which tab (and which home section) a text path belongs to. */
export function locate(path: string): { tab: TabKey; sectionId?: string } {
	const [head, second] = path.split(".");
	if (head === "home") return { tab: "home", sectionId: second?.replace(/\[.*$/, "") };
	if (head === "pricing") return { tab: "pricing" };
	if (head === "features" || head === "about" || head === "contact") return { tab: "pages" };
	return { tab: "menu" };
}

/** Every text of the enabled parts, in page order, with the path the engine would report. */
export function allTexts(content: LandingContent): { path: string; text: LText }[] {
	const out: { path: string; text: LText }[] = [];
	const walk = (data: unknown, path: string) => {
		if (isLText(data)) {
			out.push({ path, text: data });
		} else if (Array.isArray(data)) {
			data.forEach((item, i) => {
				walk(item, `${path}[${i}]`);
			});
		} else if (data && typeof data === "object") {
			for (const [k, v] of Object.entries(data)) walk(v, path ? `${path}.${k}` : k);
		}
	};
	const dump = content as unknown as Record<string, Record<string, unknown> & { enabled?: boolean }>;
	for (const s of content.home) {
		if (s.enabled === false) continue;
		const { id, type: _type, ...rest } = s as unknown as { id: string; type: string };
		walk(rest, `home.${id}`);
	}
	walk(dump.nav, "nav");
	walk(dump.footer, "footer");
	walk(dump.seo, "seo");
	for (const page of ["pricing", "features", "about", "contact"] as const) {
		if (dump[page]?.enabled !== false) walk(dump[page], page);
	}
	if (dump.announcement?.enabled) walk(dump.announcement, "announcement");
	if (dump.ask?.enabled) walk(dump.ask, "ask");
	return out;
}

/** Texts a language lacks (the default language is never short; publishing refuses gaps). */
export function missingIn(content: LandingContent, lang: LandingLang): string[] {
	if (lang === content.default_language) return [];
	return allTexts(content)
		.filter(({ text }) => !text[lang]?.trim())
		.map(({ path }) => path);
}

/** Replace one language of a text; an empty string removes it (the default language stays). */
export function withLang(text: LText | null | undefined, lang: string, value: string, keep: string): LText {
	const next: LText = { ...(text ?? {}) };
	if (value === "" && lang !== keep) delete next[lang];
	else next[lang] = value;
	return next;
}

// ---------------------------------------------------------------------------------- sections

/** Can this section move by `delta` places without passing the first or last one? */
export function canMove(home: Section[], id: string, delta: -1 | 1): boolean {
	const index = home.findIndex((s) => s.id === id);
	if (index < 0) return false;
	const type = home[index]?.type;
	if (type === FIRST_SECTION || type === LAST_SECTION) return false;
	const target = index + delta;
	return target > 0 && target < home.length - 1;
}

export function moveSection(home: Section[], id: string, delta: -1 | 1): Section[] {
	if (!canMove(home, id, delta)) return home;
	const index = home.findIndex((s) => s.id === id);
	const next = [...home];
	const [item] = next.splice(index, 1);
	if (item) next.splice(index + delta, 0, item);
	return next;
}

/** Move to an exact place (drag and drop), under the same rules. */
export function moveSectionTo(home: Section[], id: string, target: number): Section[] {
	const index = home.findIndex((s) => s.id === id);
	if (index < 0 || target === index) return home;
	if (target < 1 || target > home.length - 2) return home;
	const item = home[index];
	if (!item || item.type === FIRST_SECTION || item.type === LAST_SECTION) return home;
	const next = [...home];
	next.splice(index, 1);
	next.splice(target, 0, item);
	return next;
}

export function canSwitchOff(section: Section): boolean {
	return section.type !== FIRST_SECTION && section.type !== LAST_SECTION;
}

// ----------------------------------------------------------------------------------- presets

export const PRESET_PARTS = ["conversation", "pain_wall", "languages", "whatsapp_journey", "faq"] as const;
export type PresetPart = (typeof PRESET_PARTS)[number];

/** Merge the ticked parts of a starting example into a copy of the draft (nothing is saved). */
export function mergePreset(
	content: LandingContent,
	preset: { conversation: unknown; sections: Record<string, unknown> },
	parts: readonly PresetPart[],
): LandingContent {
	const next = structuredClone(content) as LandingContent;
	for (const section of next.home as unknown as Record<string, unknown>[]) {
		if (section.type === "hero" && parts.includes("conversation")) {
			section.conversation = structuredClone(preset.conversation);
			continue;
		}
		const type = String(section.type);
		if (type !== "hero" && (parts as readonly string[]).includes(type) && type in preset.sections) {
			const source = preset.sections[type] as Record<string, unknown>;
			for (const [k, v] of Object.entries(source)) {
				if (k === "id" || k === "type" || k === "enabled") continue;
				section[k] = structuredClone(v);
			}
		}
	}
	return next;
}

// -------------------------------------------------------------------------------- comparison

/** Two drafts are the same when their JSON is (key order does not matter to the server). */
export function sameContent(a: LandingContent, b: LandingContent): boolean {
	return stable(a) === stable(b);
}

function stable(value: unknown): string {
	return JSON.stringify(value, (_k, v) =>
		v && typeof v === "object" && !Array.isArray(v)
			? Object.fromEntries(Object.entries(v as Record<string, unknown>).sort(([x], [y]) => x.localeCompare(y)))
			: v,
	);
}

// ---------------------------------------------------------------------------------- limits

/** The longest a text may be, from the `limits` the engine sent (`hero.headline` -> 60). */
export function limitOf(limits: Record<string, number>, key: string): number | null {
	return limits[key] ?? null;
}

/** Count what a person sees as characters (Hindi combining marks count as one with their base). */
export function charCount(value: string): number {
	return [...value].length;
}

export function clampTo(value: string, max: number | null): string {
	if (max == null) return value;
	const chars = [...value];
	return chars.length <= max ? value : chars.slice(0, max).join("");
}

// -------------------------------------------------------------------------------- languages

/** Two trees of the same shape, each in one language: the texts of both, side by side. */
export function mergeTexts(a: unknown, b: unknown): unknown {
	if (isLText(a) && isLText(b)) return { ...a, ...b };
	if (Array.isArray(a) && Array.isArray(b)) return a.map((x, i) => (i < b.length ? mergeTexts(x, b[i]) : x));
	if (a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) && !Array.isArray(b)) {
		const other = b as Record<string, unknown>;
		return Object.fromEntries(
			Object.entries(a as Record<string, unknown>).map(([k, v]) => [k, k in other ? mergeTexts(v, other[k]) : v]),
		);
	}
	return a;
}

/**
 * A written conversation into the hero's: in the site's default language it replaces the sample;
 * in another language it adds that language's texts to the lines already there, and is refused
 * (null) when the two have a different number of lines.
 */
export function applyGenerated<T extends { turns: unknown[] }>(
	existing: T,
	written: T,
	lang: LandingLang,
	defaultLang: LandingLang,
): T | null {
	if (lang === defaultLang) return { ...written, generated: true } as T;
	if (existing.turns.length !== written.turns.length) return null;
	return { ...(mergeTexts(existing, written) as T), generated: true };
}

// ------------------------------------------------------------------------------------ labels

const PAGE_NAMES: Record<string, string> = {
	pricing: "Pricing page",
	features: "Features page",
	about: "About page",
	contact: "Contact page",
	nav: "Menu",
	footer: "Footer",
	seo: "Search and sharing texts",
	announcement: "Bar at the top",
	ask: "Ask-it-yourself button",
};

function humanise(rest: string[]): string {
	const text = rest
		.join(" › ")
		.replace(/\[(\d+)\]/g, (_m, n: string) => ` ${Number(n) + 1}`)
		.replace(/[_-]/g, " ")
		.trim();
	return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A path the engine reports, in words: `home.pain-wall.questions[0].text` -> "Questions your customers ask late at night · Questions 1 › text". */
export function describePath(path: string, content: LandingContent): string {
	const [head = "", ...rest] = path.split(".");
	if (head === "home") {
		const [id = "", ...field] = rest;
		const section = content.home.find((s) => s.id === id || id.startsWith(`${s.id}[`));
		const title = (section && SECTION_TITLES[section.type]?.title) ?? id;
		return field.length > 0 ? `${title} · ${humanise(field)}` : title;
	}
	const page = PAGE_NAMES[head] ?? humanise([head]);
	return rest.length > 0 ? `${page} · ${humanise(rest)}` : page;
}
