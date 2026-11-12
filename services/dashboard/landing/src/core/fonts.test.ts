import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { copyFonts } from "../../fonts";

function fakePackage(root: string, pkg: string, files: string[]) {
	const dir = path.join(root, "node_modules", pkg, "files");
	mkdirSync(dir, { recursive: true });
	for (const name of files) writeFileSync(path.join(dir, name), `font:${name}`);
}

describe("copyFonts", () => {
	it("copies the files that are installed under hashed names and skips the rest", () => {
		const root = mkdtempSync(path.join(os.tmpdir(), "landing-fonts-"));
		const out = path.join(root, "build");
		fakePackage(root, "@fontsource/schibsted-grotesk", [
			"schibsted-grotesk-latin-400-normal.woff2",
			"schibsted-grotesk-latin-700-normal.woff2",
		]);
		fakePackage(root, "@fontsource-variable/inter", ["inter-latin-wght-normal.woff2"]);
		const log: string[] = [];
		const manifest = copyFonts(root, out, (line) => log.push(line));
		expect(Object.keys(manifest).sort()).toEqual(["inter", "schibsted-grotesk"]);
		expect(manifest.inter?.latin).toMatch(/^fonts\/inter-latin\.[0-9a-f]{10}\.woff2$/);
		expect(manifest["schibsted-grotesk"]).toMatchObject({
			"latin@400": expect.stringMatching(/^fonts\/schibsted-grotesk-latin-400\.[0-9a-f]{10}\.woff2$/),
			"latin@700": expect.stringMatching(/^fonts\/schibsted-grotesk-latin-700\.[0-9a-f]{10}\.woff2$/),
		});
		expect(log.some((line) => line.includes("@fontsource-variable/geist is not installed"))).toBe(true);
		const written = JSON.parse(readFileSync(path.join(out, "fonts", "fonts.json"), "utf8"));
		expect(written).toEqual(manifest);
		expect(readFileSync(path.join(out, manifest.inter?.latin as string), "utf8")).toBe(
			"font:inter-latin-wght-normal.woff2",
		);
	});
});
