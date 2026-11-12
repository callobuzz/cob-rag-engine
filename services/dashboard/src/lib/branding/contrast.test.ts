import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { KB_AVATAR_COLORS } from "@/lib/kb-selection";
import { DARK_SURFACES, LIGHT_SURFACES } from "./BrandingProvider";
import { contrastRatio, foregroundOn, over, parseHex, type Rgb, readableOn, readableOnAll } from "./contrast";

/** Parse the token blocks out of globals.css so the test checks what ships. */
function tokens(block: ":root" | ".dark"): Record<string, string> {
	const css = fs.readFileSync(path.resolve(import.meta.dirname, "../../styles/globals.css"), "utf8");
	const re = block === ":root" ? /:root\s*\{([\s\S]*?)\n\}/ : /\n\.dark\s*\{([\s\S]*?)\n\}/;
	const body = re.exec(css)?.[1] ?? "";
	const out: Record<string, string> = {};
	for (const m of body.matchAll(/--([\w-]+):\s*([^;]+);/g)) if (m[1] && m[2]) out[m[1]] = m[2].trim();
	return out;
}

function rgba(v: string): { rgb: Rgb; a: number } | null {
	const hex = parseHex(v);
	if (hex) return { rgb: hex, a: 1 };
	const m = /rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/.exec(v);
	return m ? { rgb: [Number(m[1]), Number(m[2]), Number(m[3])], a: Number(m[4]) } : null;
}

const light = tokens(":root");
const dark = { ...light, ...tokens(".dark") };

function solid(t: Record<string, string>, name: string, under = "card"): Rgb {
	const v = rgba(t[name] ?? "");
	const base = parseHex(t[under] ?? "#ffffff") ?? [255, 255, 255];
	if (!v) throw new Error(`token --${name} missing or not a colour: ${t[name]}`);
	return v.a === 1 ? v.rgb : over(v.rgb, v.a, base);
}

// [text, background] pairs that carry text in the UI.
const PAIRS: [string, string][] = [
	["foreground", "background"],
	["foreground", "card"],
	["foreground", "muted"],
	["foreground", "secondary"],
	["foreground", "accent"],
	["foreground-2", "background"],
	["foreground-2", "card"],
	["foreground-2", "muted"],
	["muted-foreground", "background"],
	["muted-foreground", "card"],
	["muted-foreground", "muted"],
	["muted-foreground", "secondary"],
	["primary-foreground", "primary"],
	["primary", "card"],
	["primary", "background"],
	["warning", "card"],
	["warning", "background"],
	["success", "card"],
	["destructive", "card"],
	["destructive", "background"],
	["info", "card"],
	["violet", "card"],
	// status text on its own soft tint (badges, alerts)
];

// Status text on its own soft tint (badges, alerts), with the tint laid over each page surface.
const SOFT: [string, string][] = [
	["success", "success-soft"],
	["warning", "warning-soft"],
	["destructive", "destructive-soft"],
	["primary", "primary-soft"],
];

describe("design tokens meet WCAG AA (4.5:1) in both themes", () => {
	for (const [theme, t] of [
		["light", light],
		["dark", dark],
	] as const) {
		for (const [fg, bg] of PAIRS) {
			it(`${theme}: --${fg} on --${bg}`, () => {
				const ratio = contrastRatio(solid(t, fg), solid(t, bg));
				expect(ratio, `${theme} ${fg}/${bg} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(4.5);
			});
		}
		for (const [fg, soft] of SOFT) {
			for (const under of ["background", "card", "muted"]) {
				it(`${theme}: --${fg} on --${soft} over --${under}`, () => {
					const ratio = contrastRatio(solid(t, fg), solid(t, soft, under));
					expect(ratio, `${theme} ${fg}/${soft}/${under} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(4.5);
				});
			}
		}
	}
});

describe("brand contrast", () => {
	it("chooses white on dark brand colours and dark ink on light ones", () => {
		expect(foregroundOn("#0b7a5f")).toBe("#ffffff");
		expect(foregroundOn("#3fd4a8")).toBe("#04101f");
		expect(foregroundOn("#2f8cff")).toBe("#04101f");
	});

	it("adjusts brand link colour until it reads on each page background", () => {
		for (const brand of ["#0b7a5f", "#3fd4a8", "#f2b441", "#2f8cff", "#000000", "#ffffff"]) {
			expect(contrastRatio(readableOn(brand, "#f5f7fb"), "#f5f7fb")).toBeGreaterThanOrEqual(4.5);
			expect(contrastRatio(readableOn(brand, "#07111d"), "#07111d")).toBeGreaterThanOrEqual(4.5);
		}
	});

	it("brand links stay readable on every surface of each theme", () => {
		for (const brand of ["#0b7a5f", "#3fd4a8", "#2f8cff", "#7c3aed", "#f2b441"]) {
			for (const [surfaces, name] of [
				[LIGHT_SURFACES, "light"],
				[DARK_SURFACES, "dark"],
			] as const) {
				const c = readableOnAll(brand, [...surfaces]);
				for (const s of surfaces) expect(contrastRatio(c, s), `${brand} ${name} ${s}`).toBeGreaterThanOrEqual(4.5);
			}
		}
	});

	it("every KB avatar colour is AA with white text", () => {
		for (const c of KB_AVATAR_COLORS) expect(contrastRatio("#ffffff", c), c).toBeGreaterThanOrEqual(4.5);
	});
});
