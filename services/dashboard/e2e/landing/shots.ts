/**
 * Screenshots for human review. They go to `LANDING_SHOTS_DIR` (the session scratchpad the
 * orchestrator gives), else a folder in the OS temp directory; never the repository.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import type { Page, TestInfo } from "@playwright/test";

export function shotsDir(): string {
	const dir = process.env.LANDING_SHOTS_DIR || path.join(os.tmpdir(), "cob-landing-shots");
	fs.mkdirSync(dir, { recursive: true });
	return dir;
}

/** Full page and first screen, named `<name>--<project>--full|first.png`. */
export async function shoot(page: Page, info: TestInfo, name: string): Promise<string[]> {
	const base = path.join(shotsDir(), `${name}--${info.project.name}`);
	const full = `${base}--full.png`;
	const first = `${base}--first.png`;
	await page.screenshot({ path: first });
	await page.screenshot({ path: full, fullPage: true });
	return [first, full];
}
