import { z } from "zod";

/**
 * Runtime configuration from `public/config.js` (`globalThis.RAG_CONFIG`), validated once.
 * The engine URL may be overridden only on the key sign-in page ("Advanced") and in
 * Settings → Connection while signed in with a key; the override is stored with that
 * credential (src/lib/auth/storage.ts) and cleared on sign-out.
 */

const httpUrl = z
	.string()
	.trim()
	.refine((v) => isHttpUrl(v), "Use an http:// or https:// address")
	.transform((v) => v.replace(/\/+$/, ""));

const ConfigSchema = z.object({
	ragUrl: httpUrl.default("http://127.0.0.1:8000"),
	scraperUrl: httpUrl.default("http://127.0.0.1:8001"),
	usdToInr: z.number().positive().default(85),
});

export type AppConfig = z.infer<typeof ConfigSchema>;

declare global {
	var RAG_CONFIG: Partial<AppConfig> | undefined;
}

export function isHttpUrl(value: string): boolean {
	try {
		const u = new URL(value);
		return u.protocol === "http:" || u.protocol === "https:";
	} catch {
		return false;
	}
}

export function normalizeUrl(value: string): string {
	return value.trim().replace(/\/+$/, "");
}

let cached: AppConfig | null = null;

export function getConfig(): AppConfig {
	if (cached) return cached;
	const parsed = ConfigSchema.safeParse(globalThis.RAG_CONFIG ?? {});
	cached = parsed.success ? parsed.data : ConfigSchema.parse({});
	return cached;
}

/** Tests only. */
export function resetConfigCache(): void {
	cached = null;
}
