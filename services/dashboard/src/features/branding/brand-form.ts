import { contrastRatio, parseHex } from "@/lib/branding/contrast";
import { specialUseTld } from "@/lib/forms/email-rule";
import { brandingStrings } from "@/lib/strings/admin";
import type { BrandingPatch, Settings } from "./api";

const t = brandingStrings.brand.errors;

/** The brand form's fields, as the inputs hold them (text). */
export const BRAND_FIELDS = [
	"product_name",
	"logo_url",
	"primary_color",
	"support_email",
	"contact_url",
	"contact_text",
] as const;
export type BrandField = (typeof BRAND_FIELDS)[number];
export type BrandValues = Record<BrandField, string>;
export type BrandErrors = Partial<Record<BrandField, string>>;

/** Optional fields: cleared → `null` (the server resets them to their default: none). */
const OPTIONAL: ReadonlySet<BrandField> = new Set(["logo_url", "support_email", "contact_url"]);
export const URL_MAX = 2048;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
export const HEX_RE = /^#[0-9a-fA-F]{6}$/;

export function brandValues(b: Settings["branding"]): BrandValues {
	return {
		product_name: b.product_name,
		logo_url: b.logo_url ?? "",
		primary_color: b.primary_color,
		support_email: b.support_email ?? "",
		contact_url: b.contact_url ?? "",
		contact_text: b.contact_text,
	};
}

/**
 * Accepts only `http:` and `https:` (never `javascript:` / `data:`). Whether `http:` is allowed
 * depends on the engine's RAG_ENV, which the browser can't see: the server's 422 decides that.
 */
export function isHttpUrl(value: string): boolean {
	try {
		const u = new URL(value);
		return (u.protocol === "https:" || u.protocol === "http:") && !!u.hostname;
	} catch {
		return false;
	}
}

/** `allowSpecialUse`: the engine is in development, so .local / .test addresses are fine (the engine's email rule). */
export function validateBrand(v: BrandValues, allowSpecialUse = false): BrandErrors {
	const e: BrandErrors = {};
	const name = v.product_name.trim();
	if (name.length < 1 || name.length > 80) e.product_name = t.name;
	for (const k of ["logo_url", "contact_url"] as const) {
		const s = v[k].trim();
		if (!s) continue;
		if (s.length > URL_MAX) e[k] = t.urlLong;
		else if (!isHttpUrl(s)) e[k] = t.url;
	}
	if (!HEX_RE.test(v.primary_color.trim())) e.primary_color = t.color;
	const email = v.support_email.trim();
	if (email && (!EMAIL_RE.test(email) || (!allowSpecialUse && specialUseTld(email)))) e.support_email = t.email;
	const contact = v.contact_text.trim();
	if (contact.length < 1 || contact.length > 500) e.contact_text = t.contact;
	return e;
}

/** Only the fields that changed; a cleared optional field is `null` (A7 PATCH semantics). */
export function brandPatch(initial: BrandValues, v: BrandValues): BrandingPatch {
	const patch: BrandingPatch = {};
	for (const k of BRAND_FIELDS) {
		const next = v[k].trim();
		if (next === initial[k].trim()) continue;
		if (k === "primary_color") patch.primary_color = next.toLowerCase();
		else patch[k] = next === "" && OPTIONAL.has(k) ? null : next;
	}
	return patch;
}

/** White text on the colour (mockup contrast hint); null for an invalid colour. */
export function whiteContrast(color: string): number | null {
	return parseHex(color) && HEX_RE.test(color.trim()) ? contrastRatio("#ffffff", color.trim()) : null;
}

/** Saving a colour below 3 : 1 against white asks for confirmation. */
export const LOW_CONTRAST = 3;

/** Server 422 paths (`branding.logo_url`) → the form's field. */
export function brandFieldOf(path: string): BrandField | null {
	const k = path.replace(/^branding\./, "");
	return (BRAND_FIELDS as readonly string[]).includes(k) ? (k as BrandField) : null;
}
