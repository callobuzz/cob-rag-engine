import { z } from "zod";
import { contrastRatio } from "@/lib/branding/contrast";
import { fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";
import type { WidgetAppearance } from "./api";

/**
 * Widget-key form rules (D6), mirroring the engine (`api/schemas/widget.py`,
 * `widget/security.py normalize_origin`): an origin is scheme + host (+ port), nothing else;
 * `*` allows any site. The dashboard is stricter than the engine about plain http: it is
 * accepted only for localhost and 127.0.0.1 (the engine would accept any http origin).
 */

export const MAX_ORIGINS = 50;
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export type OriginCheck = { ok: true; origin: string; warning?: string } | { ok: false; error: string };

/** Validate and normalise one site: `https://Shree-Labs.in/` → `https://shree-labs.in`. */
export function checkOrigin(raw: string, existing: readonly string[] = []): OriginCheck {
	const value = raw.trim();
	if (value === "*") {
		return existing.includes("*")
			? { ok: false, error: fill(t.origins.duplicate, { origin: "*" }) }
			: { ok: true, origin: "*", warning: t.origins.anyWarning };
	}
	let u: URL;
	try {
		u = new URL(value);
	} catch {
		return { ok: false, error: t.origins.invalid };
	}
	if (u.protocol !== "https:" && u.protocol !== "http:") return { ok: false, error: t.origins.invalid };
	// The path must be empty: `new URL` turns "https://x" into pathname "/", so compare the raw text.
	const afterHost = value.slice(value.indexOf("//") + 2).replace(/^[^/?#]*/, "");
	if (!u.hostname || u.username || u.password || (afterHost !== "" && afterHost !== "/")) {
		return { ok: false, error: t.origins.invalid };
	}
	if (u.protocol === "http:" && !LOCAL_HOSTS.has(u.hostname)) return { ok: false, error: t.origins.httpOnlyLocal };
	const origin = `${u.protocol}//${u.host}`.toLowerCase();
	if (existing.includes(origin)) return { ok: false, error: fill(t.origins.duplicate, { origin }) };
	return { ok: true, origin };
}

/** Split pasted text (lines, commas, spaces) into entries. */
export function splitOrigins(text: string): string[] {
	return text
		.split(/[\s,]+/)
		.map((s) => s.trim())
		.filter(Boolean);
}

export const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/** What `widget.js` shows when a field is unset (its `cfg` defaults). */
export const APPEARANCE_DEFAULTS = {
	title: t.appearance.defaults.title,
	greeting: t.appearance.defaults.greeting,
	placeholder: t.appearance.defaults.placeholder,
	primary_color: t.appearance.defaults.color,
	position: "right" as const,
	show_sources: true,
};

const tooLong = (max: number) => fill(t.appearance.tooLong, { max });

export const appearanceSchema = z.object({
	title: z.string().trim().max(80, tooLong(80)),
	greeting: z.string().trim().max(500, tooLong(500)),
	placeholder: z.string().trim().max(120, tooLong(120)),
	primary_color: z
		.string()
		.trim()
		.refine((v) => v === "" || HEX_COLOR.test(v), t.appearance.colorInvalid),
	position: z.enum(["right", "left"]),
	show_sources: z.boolean(),
});
export type AppearanceForm = z.infer<typeof appearanceSchema>;

const intIn = (min: number, max: number, message: string) =>
	z.coerce.number({ error: message }).int(message).min(min, message).max(max, message);

export const keySchema = z.object({
	name: z.string().trim().min(1, t.form.nameRequired).max(255, t.form.nameLong),
	allowed_origins: z.array(z.string()).min(1, t.form.sitesRequired).max(MAX_ORIGINS, t.form.sitesTooMany),
	rate_limit_per_minute: intIn(1, 600, t.form.rpmRange),
	daily_message_limit: intIn(0, 10_000_000, t.form.dailyRange),
});
export type KeyFormInput = z.input<typeof keySchema>;
export type KeyForm = z.output<typeof keySchema>;

export const createSchema = keySchema.extend({ appearance: appearanceSchema });
export type CreateFormInput = z.input<typeof createSchema>;
export type CreateForm = z.output<typeof createSchema>;

/** Form values from a saved appearance (unset → empty, so the widget default applies). */
export function appearanceToForm(a: WidgetAppearance | null | undefined): AppearanceForm {
	return {
		title: a?.title ?? "",
		greeting: a?.greeting ?? "",
		placeholder: a?.placeholder ?? "",
		primary_color: a?.primary_color ?? "",
		position: a?.position ?? "right",
		show_sources: a?.show_sources ?? true,
	};
}

/**
 * Body for the engine. `forPatch`: every field is sent and empty ones as null, which the engine's
 * merge removes (back to the default). For a create only the fields that are set are sent.
 */
export function appearanceToBody(f: AppearanceForm, forPatch: boolean): WidgetAppearance {
	const text = (v: string) => (v.trim() === "" ? null : v.trim());
	const body: WidgetAppearance = {
		title: text(f.title),
		greeting: text(f.greeting),
		placeholder: text(f.placeholder),
		primary_color: text(f.primary_color),
		position: f.position,
		show_sources: f.show_sources,
	};
	if (forPatch) return body;
	return Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null)) as WidgetAppearance;
}

/** The look the widget would render with (defaults filled in). */
export function effectiveAppearance(f: AppearanceForm) {
	return {
		title: f.title.trim() || APPEARANCE_DEFAULTS.title,
		greeting: f.greeting.trim() || APPEARANCE_DEFAULTS.greeting,
		placeholder: f.placeholder.trim() || APPEARANCE_DEFAULTS.placeholder,
		primary_color: HEX_COLOR.test(f.primary_color.trim()) ? f.primary_color.trim() : APPEARANCE_DEFAULTS.primary_color,
		position: f.position,
		show_sources: f.show_sources,
	};
}

/** White text on the brand colour (what widget.js draws): the ratio and whether it reaches AA. */
export function whiteTextContrast(color: string): { ratio: number; ok: boolean } {
	const ratio = contrastRatio("#ffffff", color);
	return { ratio, ok: ratio >= 4.5 };
}

/** "https://shree-labs.in" → "shree-labs.in" for chips and sentences. */
export function siteLabel(origin: string): string {
	return origin.replace(/^https?:\/\//, "");
}

/** "shree-labs.in and www.shree-labs.in" (or "all its sites" for `*`). */
export function sitesSentence(origins: readonly string[]): string {
	if (origins.length === 0 || origins.includes("*")) return t.allSites;
	const labels = origins.map(siteLabel);
	if (labels.length === 1) return labels[0] ?? "";
	return `${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}

/** Whether the preview iframe (this dashboard's origin) may use the key. */
export function previewAllowed(origins: readonly string[], origin: string): boolean {
	return origins.includes("*") || origins.includes(origin.toLowerCase());
}
