import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { getTheme, setDensity, setTheme } from "./theme";

const THEME_JS = fs.readFileSync(path.resolve(import.meta.dirname, "../../public/theme.js"), "utf8");

function runThemeScript() {
	// biome-ignore lint/security/noGlobalEval: executes the shipped no-flash script exactly as the browser does
	globalThis.eval(THEME_JS);
}

function mockSystemDark(dark: boolean) {
	window.matchMedia = ((q: string) => ({
		matches: dark && q.includes("dark"),
		media: q,
		addEventListener: () => {},
		removeEventListener: () => {},
	})) as unknown as typeof window.matchMedia;
}

describe("public/theme.js (before first paint)", () => {
	it("applies a stored dark theme and compact density", () => {
		localStorage.setItem("cob.theme", "dark");
		localStorage.setItem("cob.density", "compact");
		runThemeScript();
		expect(document.documentElement.classList.contains("dark")).toBe(true);
		expect(document.documentElement.dataset.density).toBe("compact");
	});

	it("follows the OS when the choice is system (the default)", () => {
		mockSystemDark(true);
		runThemeScript();
		expect(document.documentElement.classList.contains("dark")).toBe(true);
		mockSystemDark(false);
		runThemeScript();
		expect(document.documentElement.classList.contains("dark")).toBe(false);
	});

	it("light beats a dark OS", () => {
		mockSystemDark(true);
		localStorage.setItem("cob.theme", "light");
		runThemeScript();
		expect(document.documentElement.classList.contains("dark")).toBe(false);
	});
});

describe("theme persistence", () => {
	it("setTheme stores the choice and applies it; the next load reads it", () => {
		mockSystemDark(false);
		setTheme("dark");
		expect(localStorage.getItem("cob.theme")).toBe("dark");
		expect(document.documentElement.classList.contains("dark")).toBe(true);
		document.documentElement.className = "";
		runThemeScript();
		expect(document.documentElement.classList.contains("dark")).toBe(true);
		expect(getTheme()).toBe("dark");
		setDensity("compact");
		expect(document.documentElement.dataset.density).toBe("compact");
	});
});
