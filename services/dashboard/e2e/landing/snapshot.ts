/**
 * Renders the website to static folders with the engine's own snapshot renderer (LP1), so the
 * landing specs can look at real pages without starting the stack.
 *
 * Python comes from `LANDING_PY`, else the repository's venv. Output goes to
 * `LANDING_SNAP_DIR` (default: a folder in the OS temp directory), never the repository.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const BRANDS = ["#0f766e", "#e11d48", "#facc15"] as const;
export const PRESETS = ["services", "clinic"] as const;
export const LANGS = ["en", "hi-Latn"] as const;

const here = import.meta.dirname;
const repo = path.resolve(here, "../../../..");

export function snapRoot(): string {
	return process.env.LANDING_SNAP_DIR || path.join(os.tmpdir(), "cob-landing-snapshots");
}

export function python(): string {
	if (process.env.LANDING_PY) return process.env.LANDING_PY;
	return process.platform === "win32"
		? path.join(repo, ".venv", "Scripts", "python.exe")
		: path.join(repo, ".venv", "bin", "python");
}

export function slug(brand: string): string {
	return brand.replace("#", "");
}

/** The folder name of one snapshot; the page lives at `/<name>/`. */
export function snapName(brand: string, preset: string, lang: string, extra = ""): string {
	return [slug(brand), preset, lang.toLowerCase(), extra].filter(Boolean).join("-");
}

function render(name: string, args: string[]): void {
	const out = path.join(snapRoot(), name);
	fs.rmSync(out, { recursive: true, force: true });
	execFileSync(python(), ["-m", "callobuzz_rag.landing.snapshot", ...args, "--out", out], {
		cwd: repo,
		env: { ...process.env, PYTHONIOENCODING: "utf-8" },
		stdio: ["ignore", "ignore", "inherit"],
	});
}

/** Every combination the specs use. Returns the folder holding the shared /_landing assets. */
export function renderSnapshots(): string {
	fs.mkdirSync(snapRoot(), { recursive: true });
	for (const brand of BRANDS) {
		for (const preset of PRESETS) {
			for (const lang of LANGS) {
				render(snapName(brand, preset, lang), [
					"--content",
					`preset:${preset}`,
					"--brand",
					brand,
					"--base",
					"auto",
					"--lang",
					lang,
				]);
			}
		}
	}
	// A page that sells no WhatsApp, and one with "Ask it yourself" connected.
	render(snapName(BRANDS[0], "services", "en", "nowa"), [
		"--content",
		"preset:services",
		"--brand",
		BRANDS[0],
		"--base",
		"auto",
		"--no-whatsapp",
	]);
	render(snapName(BRANDS[0], "services", "en", "ask"), [
		"--content",
		"preset:services",
		"--brand",
		BRANDS[0],
		"--base",
		"auto",
		"--ask-key",
		"pk_demo_landing_key",
	]);
	// A page with a background video behind the hero, for the speed test.
	render(snapName(BRANDS[0], "services", "en", "backdrop"), [
		"--content",
		"preset:services",
		"--brand",
		BRANDS[0],
		"--base",
		"auto",
		"--pages",
		"home",
		"--backdrop-demo",
	]);
	return path.join(snapRoot(), snapName(BRANDS[0], "services", "en"));
}
