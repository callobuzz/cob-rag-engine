const ALLOWED = ["http", "https", "mailto", "tel"];
const NAMED: Record<string, string> = {
	amp: "&",
	lt: "<",
	gt: ">",
	quot: '"',
	apos: "'",
	colon: ":",
	tab: "\t",
	newline: "\n",
	lpar: "(",
	rpar: ")",
	sol: "/",
};

/** Decodes numeric and a few named entities, once (a browser decodes an attribute once). */
function decodeOnce(s: string): string {
	return s.replace(/&(?:#(\d{1,7})|#[xX]([0-9a-fA-F]{1,6})|([a-zA-Z]+));?/g, (whole, dec, hex, name) => {
		if (name) return NAMED[name.toLowerCase()] ?? whole;
		const cp = dec ? Number(dec) : Number.parseInt(hex, 16);
		return cp > 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : whole;
	});
}

// biome-ignore lint/suspicious/noControlCharactersInRegex: stripping control characters is the point
const INVISIBLE = /[\u0000-\u001f\u007f-\u009f\u00ad\u200b-\u200d\u2060\ufeff]/g;

/**
 * The only gate a link target passes: trim, drop control and invisible characters, decode entities
 * once (to check, never to return), allow only http, https, mailto and tel. Returns the trimmed
 * target as written, or null (the link renders as plain text). Scheme-less and relative targets are refused too.
 */
export function safeUrl(url: string): string | null {
	const strip = (s: string) => s.replace(INVISIBLE, "");
	const raw = strip(String(url).trim()).trim();
	// The decoded form is only for checking (what a browser might see); the raw form is returned.
	const dec = strip(decodeOnce(raw)).trim();
	return allowed(raw) && allowed(dec) ? raw : null;
}

function allowed(s: string): boolean {
	if (!s || s.length > 2048) return false;
	const m = /^([a-z][a-z0-9+.-]*):/i.exec(s);
	const scheme = (m?.[1] ?? "").toLowerCase();
	if (!m || !ALLOWED.includes(scheme)) return false;
	const rest = s.slice(m[0].length);
	if (scheme === "http" || scheme === "https") return /^\/\/[^\s/?#\\]+/.test(rest);
	return rest.length > 0;
}

/**
 * A target on this site: `/docs/screens/widget`, `/docs/x#anchor` or `#anchor`. Refuses `//host`,
 * backslashes, control characters and anything with a scheme. Only the Docs page variant asks.
 */
export function internalUrl(url: string): string | null {
	const raw = String(url).trim();
	// biome-ignore lint/suspicious/noControlCharactersInRegex: refusing control characters is the point
	if (!raw || raw.length > 2048 || /[\u0000-\u001f\u007f\\\s]/.test(raw)) return null;
	if (raw.startsWith("#")) return raw.length > 1 ? raw : null;
	return raw.startsWith("/") && !raw.startsWith("//") ? raw : null;
}
