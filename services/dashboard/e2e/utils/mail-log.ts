import { execFileSync, spawnSync } from "node:child_process";
import path from "node:path";

/**
 * Reads e-mailed codes and links from the dev e-mail log provider (A2 `LogEmailProvider`) via
 * `docker compose logs mailer app`. Read-only: never restarts or changes containers.
 * Used by auth.spec.ts against a real stack (A3–A5 merged).
 */
const REPO_ROOT = path.resolve(import.meta.dirname, "../../../..");

/**
 * E2E_MAIL_CONTAINER / E2E_APP_CONTAINER: read from named one-off containers (`docker logs`)
 * instead of the compose services, for a stack started with `docker compose run --name …`.
 */
export function composeLogs(services: string[] = ["mailer", "app"], since = "10m"): string {
	const named = services.flatMap((s) => {
		const c = s === "mailer" ? process.env.E2E_MAIL_CONTAINER : s === "app" ? process.env.E2E_APP_CONTAINER : undefined;
		return c ? [c] : [];
	});
	if (named.length > 0 && named.length === services.length) {
		return named
			.map((c) => {
				const r = spawnSync("docker", ["logs", "--since", since, c], { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });
				return r.stdout + r.stderr;
			})
			.join("\n");
	}
	return execFileSync("docker", ["compose", "logs", "--no-color", "--since", since, ...services], {
		cwd: REPO_ROOT,
		encoding: "utf8",
		maxBuffer: 32 * 1024 * 1024,
	});
}

/** The newest 6-digit code sent to `email` (verify / reset / set-password). */
export function latestCodeFor(email: string): string | null {
	const logs = composeLogs();
	const blocks = logs.split(/DEV EMAIL/).filter((b) => b.includes(`to=${email}`) || b.includes(email));
	for (const block of blocks.reverse()) {
		const m = /\b(\d{6})\b/.exec(block);
		if (m?.[1]) return m[1];
	}
	return null;
}

/** The super-admin bootstrap link printed by `python -m callobuzz_rag.accounts.bootstrap`. */
export function latestSetPasswordLink(): string | null {
	const m = composeLogs(["app"], "24h").match(/\/set-password#email=[^\s"']+&code=\d{6}/g);
	return m?.at(-1) ?? null;
}

export function latestInviteToken(): string | null {
	const m = composeLogs().match(/invite#token=(inv_[A-Za-z0-9_-]+)/g);
	return m?.at(-1)?.split("token=")[1] ?? null;
}
