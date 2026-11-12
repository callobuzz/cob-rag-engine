/** WCAG 2.x contrast helpers (README "Branding": foreground on --brand chosen by contrast). */

export type Rgb = [number, number, number];

export function parseHex(hex: string): Rgb | null {
	const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(hex.trim());
	if (!m?.[1]) return null;
	let h = m[1];
	if (h.length === 3) h = h.replace(/./g, (c) => c + c);
	return [Number.parseInt(h.slice(0, 2), 16), Number.parseInt(h.slice(2, 4), 16), Number.parseInt(h.slice(4, 6), 16)];
}

export function toHex([r, g, b]: Rgb): string {
	return `#${[r, g, b]
		.map((v) =>
			Math.round(Math.max(0, Math.min(255, v)))
				.toString(16)
				.padStart(2, "0"),
		)
		.join("")}`;
}

function channel(v: number): number {
	const s = v / 255;
	return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(rgb: Rgb): number {
	return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

export function contrastRatio(a: string | Rgb, b: string | Rgb): number {
	const ra = typeof a === "string" ? parseHex(a) : a;
	const rb = typeof b === "string" ? parseHex(b) : b;
	if (!ra || !rb) return 1;
	const la = luminance(ra);
	const lb = luminance(rb);
	return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Composite an rgba colour over an opaque background. */
export function over(fg: Rgb, alpha: number, bg: Rgb): Rgb {
	return [
		fg[0] * alpha + bg[0] * (1 - alpha),
		fg[1] * alpha + bg[1] * (1 - alpha),
		fg[2] * alpha + bg[2] * (1 - alpha),
	];
}

export const DARK_INK = "#04101f";

/** Text colour on a brand-coloured surface: white if it reaches 4.5:1, else the dark ink. */
export function foregroundOn(brand: string): string {
	return contrastRatio("#ffffff", brand) >= 4.5 ? "#ffffff" : DARK_INK;
}

/**
 * Brand colour adjusted to read as text (links) on a given page background: mixed toward
 * black (light theme) or white (dark theme) in small steps until it reaches `target`.
 */
export function readableOn(brand: string, background: string, target = 4.5): string {
	const b = parseHex(brand);
	const bg = parseHex(background);
	if (!b || !bg) return brand;
	if (contrastRatio(b, bg) >= target) return toHex(b);
	const towards: Rgb = luminance(bg) > 0.5 ? [0, 0, 0] : [255, 255, 255];
	for (let t = 0.05; t <= 1; t += 0.05) {
		const c = over(towards, t, b);
		if (contrastRatio(c, bg) >= target) return toHex(c);
	}
	return toHex(towards);
}

/** Like readableOn, but the result must reach `target` on every background given. */
export function readableOnAll(brand: string, backgrounds: string[], target = 4.5): string {
	const bgs = backgrounds.map(parseHex).filter((b): b is Rgb => !!b);
	const b = parseHex(brand);
	if (!b || bgs.length === 0) return brand;
	const ok = (c: Rgb) => bgs.every((bg) => contrastRatio(c, bg) >= target);
	if (ok(b)) return toHex(b);
	const towards: Rgb = luminance(bgs[0] as Rgb) > 0.5 ? [0, 0, 0] : [255, 255, 255];
	for (let t = 0.05; t <= 1; t += 0.05) {
		const c = over(towards, t, b);
		if (ok(c)) return toHex(c);
	}
	return toHex(towards);
}
