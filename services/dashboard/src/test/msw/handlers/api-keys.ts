import { HttpResponse, http } from "msw";
import type { ApiKey } from "@/features/keys/api";
import { BASE, CORPORA, SHREE } from "../data";

/**
 * Named API keys, stateful per test (reset in setup.ts): the three mounts of the engine's key
 * routes (knowledge base, the caller's workspace, a customer for the operator). Contract:
 * docs/openapi.json (`/corpora/{id}/keys`, `/tenants/me/keys`, `/admin/tenants/{id}/keys`).
 * Secrets are made up: `ck_<id8>_new_<n>` and `ck_<id8>_rot_<n>`.
 */

const PATIENT = CORPORA[0]?.id ?? "";
export const DEFAULT_KEY_ID = "6b000000-0000-4000-8000-000000000001";
export const AGENT_KEY_ID = "6b000000-0000-4000-8000-000000000002";
export const SELF_KEY_ID = "6b000000-0000-4000-8000-0000000000a1";
const HOUR = 3_600_000;

function seed(): Map<string, ApiKey[]> {
	const now = Date.now();
	const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
	return new Map<string, ApiKey[]>([
		[
			`kb:${PATIENT}`,
			[
				{
					id: DEFAULT_KEY_ID,
					name: "Default key",
					key_prefix: "ck_7b221003_...",
					permission: "full",
					is_default: true,
					created_by: "migrated",
					created_at: "2026-09-20T10:00:00Z",
					last_used_at: iso(72 * HOUR),
				},
				{
					id: AGENT_KEY_ID,
					name: "Agent – Claude Code",
					key_prefix: "ck_7b221003_Xy7k...",
					permission: "read",
					is_default: false,
					created_by: "priya@shree-labs.in",
					created_at: "2026-09-28T10:00:00Z",
					last_used_at: iso(2 * HOUR),
				},
			],
		],
		[
			`tenant:${SHREE.id}`,
			[
				{
					id: SELF_KEY_ID,
					name: "Default key",
					key_prefix: "tk_11111111_...",
					permission: "full",
					is_default: true,
					created_by: "migrated",
					created_at: "2026-09-10T10:00:00Z",
					last_used_at: iso(HOUR),
				},
				{
					id: "6b000000-0000-4000-8000-0000000000a2",
					name: "n8n automation",
					key_prefix: "tk_11111111_Qw3e...",
					permission: "write",
					is_default: false,
					created_by: "admin key",
					created_at: "2026-09-25T10:00:00Z",
					last_used_at: null,
				},
			],
		],
	]);
}

let keys = seed();
let n = 0;
export const keysCalls: { method: string; path: string; body?: unknown }[] = [];
export function resetApiKeysState(): void {
	keys = seed();
	n = 0;
	keysCalls.length = 0;
}

/** The keys of an owner ("kb:<id>" or "tenant:<id>") as they are now. */
export function keysOf(owner: string): ApiKey[] {
	return keys.get(owner) ?? [];
}

/** `ck_7b221003` / `tk_11111111`: the owner id's first eight characters, as in a real key. */
function id8(owner: string): string {
	return (owner.split(":")[1] ?? "").slice(0, 8);
}

const notFound = () => HttpResponse.json({ detail: "API key not found", code: "key_not_found" }, { status: 404 });

function find(owner: string, id: string): ApiKey | undefined {
	return keysOf(owner).find((k) => k.id === id);
}

function routes(prefix: string, ownerOf: (params: Record<string, string | readonly string[] | undefined>) => string) {
	const p = (params: Record<string, string | readonly string[] | undefined>) => ownerOf(params);
	return [
		http.get(`${BASE}${prefix}`, ({ params }) => {
			keysCalls.push({ method: "GET", path: `${prefix}` });
			return HttpResponse.json({ keys: keysOf(p(params)) });
		}),
		http.post(`${BASE}${prefix}`, async ({ params, request }) => {
			const body = (await request.json()) as { name: string; permission?: ApiKey["permission"] };
			keysCalls.push({ method: "POST", path: prefix, body });
			const owner = p(params);
			n++;
			const row: ApiKey = {
				id: `6b000000-0000-4000-8000-${String(100 + n).padStart(12, "0")}`,
				name: body.name,
				key_prefix: `${owner.startsWith("kb:") ? "ck" : "tk"}_${id8(owner)}_N${n}...`,
				permission: body.permission ?? "read",
				is_default: false,
				created_by: "priya@shree-labs.in",
				created_at: new Date().toISOString(),
				last_used_at: null,
			};
			keys.set(owner, [...keysOf(owner), row]);
			const kind = owner.startsWith("kb:") ? "ck" : "tk";
			return HttpResponse.json({ ...row, api_key: `${kind}_${id8(owner)}_new_${n}` }, { status: 201 });
		}),
		http.patch(`${BASE}${prefix}/:keyId`, async ({ params, request }) => {
			const body = (await request.json()) as Partial<Pick<ApiKey, "name" | "permission">>;
			keysCalls.push({ method: "PATCH", path: `${prefix}/:keyId`, body });
			const row = find(p(params), String(params.keyId));
			if (!row) return notFound();
			if (body.name !== undefined) row.name = body.name;
			if (body.permission !== undefined) row.permission = body.permission;
			return HttpResponse.json(row);
		}),
		http.post(`${BASE}${prefix}/:keyId/rotate`, ({ params }) => {
			keysCalls.push({ method: "POST", path: `${prefix}/:keyId/rotate` });
			const owner = p(params);
			const row = find(owner, String(params.keyId));
			if (!row) return notFound();
			n++;
			row.last_used_at = null;
			const kind = owner.startsWith("kb:") ? "ck" : "tk";
			return HttpResponse.json({ id: row.id, key_prefix: row.key_prefix, api_key: `${kind}_${id8(owner)}_rot_${n}` });
		}),
		http.delete(`${BASE}${prefix}/:keyId`, ({ params }) => {
			keysCalls.push({ method: "DELETE", path: `${prefix}/:keyId` });
			const owner = p(params);
			if (!find(owner, String(params.keyId))) return notFound();
			keys.set(
				owner,
				keysOf(owner).filter((k) => k.id !== params.keyId),
			);
			return new HttpResponse(null, { status: 204 });
		}),
	];
}

export const apiKeysHandlers = [
	...routes("/api/v1/corpora/:id/keys", (params) => `kb:${String(params.id)}`),
	...routes("/api/v1/tenants/me/keys", () => `tenant:${SHREE.id}`),
	...routes("/api/v1/admin/tenants/:id/keys", (params) => `tenant:${String(params.id)}`),
];
