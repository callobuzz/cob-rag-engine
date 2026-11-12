/**
 * Copies the website's web fonts out of the installed Fontsource packages into the engine's build
 * folder (`src/callobuzz_rag/landing/static/build/fonts/`) and writes `fonts.json`, the manifest
 * the engine reads (`landing/fonts.py`): `{ family: { "latin": file, "latin@700": file, ... } }`.
 * Files get a content hash in their name, so they can be cached for a year.
 *
 * Run by `pnpm build:landing` before Vite. A family whose package is not installed is skipped
 * with a message (the page then falls back to the next font in its stack), so the build never
 * fails on a missing font; `fonts.py` simply finds nothing for it.
 *
 *   node --experimental-strip-types landing/fonts.ts
 */
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

type Subset = "latin" | "latin-ext" | "devanagari";

/** Latin plus latin-ext: the rupee sign (U+20B9) lives in latin-ext. */
const LATIN: Subset[] = ["latin", "latin-ext"];
type Source = {
	/** Key in `landing/fonts.py` FAMILIES. */
	key: string;
	pkg: string;
	/** The file stem inside the package (`inter-tight`, `dm-sans`). */
	stem: string;
	/** variable: one `-wght-normal` file; static: one file per weight. */
	kind: "variable" | "static";
	weights?: number[];
	subsets: Subset[];
};

const variable = (key: string, subsets: Subset[] = LATIN): Source => ({
	key,
	pkg: `@fontsource-variable/${key}`,
	stem: key,
	kind: "variable",
	subsets,
});
const fixed = (key: string, weights: number[], subsets: Subset[] = LATIN): Source => ({
	key,
	pkg: `@fontsource/${key}`,
	stem: key,
	kind: "static",
	weights,
	subsets,
});

export const SOURCES: Source[] = [
	variable("inter-tight"),
	variable("inter"),
	variable("geist"),
	variable("geist-mono"),
	fixed("instrument-serif", [400]),
	variable("fraunces"),
	variable("manrope"),
	variable("bricolage-grotesque"),
	variable("dm-sans"),
	fixed("schibsted-grotesk", [400, 600, 700]),
	fixed("red-hat-text", [400, 600]),
	variable("noto-sans-devanagari", ["devanagari"]),
	variable("noto-serif-devanagari", ["devanagari"]),
	fixed("mukta", [400, 600], ["devanagari"]),
	fixed("hind", [400, 600], ["devanagari"]),
];

export type FontManifest = Record<string, Record<string, string>>;

function sourceFile(root: string, source: Source, subset: Subset, weight?: number): string {
	const name =
		source.kind === "variable"
			? `${source.stem}-${subset}-wght-normal.woff2`
			: `${source.stem}-${subset}-${weight}-normal.woff2`;
	return path.join(root, "node_modules", source.pkg, "files", name);
}

function printLine(line: string): void {
	process.stdout.write(`${line}\n`);
}

export function copyFonts(root: string, outDir: string, log: (line: string) => void = printLine): FontManifest {
	const fontsDir = path.join(outDir, "fonts");
	rmSync(fontsDir, { recursive: true, force: true });
	mkdirSync(fontsDir, { recursive: true });
	const manifest: FontManifest = {};
	for (const source of SOURCES) {
		const entries: Record<string, string> = {};
		for (const subset of source.subsets) {
			const weights: (number | undefined)[] = source.kind === "variable" ? [undefined] : (source.weights ?? []);
			for (const weight of weights) {
				const from = sourceFile(root, source, subset, weight);
				if (!existsSync(from)) continue;
				const data = readFileSync(from);
				const hash = createHash("sha256").update(data).digest("hex").slice(0, 10);
				const suffix = weight === undefined ? "" : `-${weight}`;
				const file = `fonts/${source.key}-${subset}${suffix}.${hash}.woff2`;
				copyFileSync(from, path.join(outDir, file));
				entries[weight === undefined ? subset : `${subset}@${weight}`] = file;
			}
		}
		if (Object.keys(entries).length > 0) manifest[source.key] = entries;
		else log(`fonts: ${source.pkg} is not installed, skipped`);
	}
	writeFileSync(path.join(fontsDir, "fonts.json"), `${JSON.stringify(manifest, null, 2)}\n`);
	return manifest;
}

if (import.meta.main) {
	const root = path.resolve(import.meta.dirname, "..");
	const outDir = path.resolve(root, "../../src/callobuzz_rag/landing/static/build");
	// Hashed scripts and styles of earlier builds go; Vite writes the new ones and the manifest.
	rmSync(path.join(outDir, "assets"), { recursive: true, force: true });
	rmSync(path.join(outDir, ".vite"), { recursive: true, force: true });
	const manifest = copyFonts(root, outDir);
	printLine(`fonts: ${Object.keys(manifest).length} families copied`);
}
