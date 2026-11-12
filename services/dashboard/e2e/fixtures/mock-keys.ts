/**
 * Named API keys part of the fake engine: the three mounts of the engine's key routes (a knowledge
 * base's `/corpora/{id}/keys`, the caller's `/tenants/me/keys`, a customer's
 * `/admin/tenants/{id}/keys` for the operator). Every key route is owner-only (`owner_required`
 * for a member). Secrets are made up and returned once, by create and rotate. Indian demo data.
 */

import { randomUUID } from "node:crypto";

export type MockPermission = "read" | "write" | "full";

export interface MockApiKey {
	id: string;
	/** `kb:<corpus id>` or `tenant:<tenant id>`. */
	owner: string;
	name: string;
	key_prefix: string;
	permission: MockPermission;
	is_default: boolean;
	created_by: string | null;
	created_at: string;
	last_used_at: string | null;
}

export interface KeysState {
	keys: MockApiKey[];
	/** Every write the dashboard sent, for assertions. */
	sent: { method: string; path: string; body: Record<string, unknown> }[];
	/** The secrets handed out, newest last. */
	secrets: { keyId: string; secret: string }[];
	/** Knowledge bases whose "Default key" was made (every knowledge base has one from creation). */
	seeded: Set<string>;
}

export interface KeysCtx {
	role: "owner" | "member" | null;
	isAdmin: boolean;
	/** The caller's own workspace, when it is a workspace principal. */
	tenantId: string | undefined;
	/** Whether the caller may reach this knowledge base. */
	canUseKb: (corpusId: string) => boolean;
	/** The e-mail or label stored as `created_by`. */
	actor: string;
	reply: (status: number, body?: unknown) => Promise<void>;
}

const HOUR = 3_600_000;

/** Shree Labs' knowledge base and workspace start with their migrated "Default key" and one more. */
export function createKeysState(shreeTenantId: string, patientCorpusId: string): KeysState {
	const now = Date.now();
	const iso = (msAgo: number) => new Date(now - msAgo).toISOString();
	const kb = `kb:${patientCorpusId}`;
	const tenant = `tenant:${shreeTenantId}`;
	return {
		keys: [
			{
				id: randomUUID(),
				owner: kb,
				name: "Default key",
				key_prefix: `ck_${patientCorpusId.slice(0, 8)}_...`,
				permission: "full",
				is_default: true,
				created_by: "migrated",
				created_at: "2026-09-20T10:00:00Z",
				last_used_at: iso(72 * HOUR),
			},
			{
				id: randomUUID(),
				owner: kb,
				name: "Agent – Claude Code",
				key_prefix: `ck_${patientCorpusId.slice(0, 8)}_Xy7k...`,
				permission: "read",
				is_default: false,
				created_by: "priya@shree-labs.in",
				created_at: "2026-09-28T10:00:00Z",
				last_used_at: iso(2 * HOUR),
			},
			{
				id: randomUUID(),
				owner: tenant,
				name: "Default key",
				key_prefix: `tk_${shreeTenantId.slice(0, 8)}_...`,
				permission: "full",
				is_default: true,
				created_by: "migrated",
				created_at: "2026-09-10T10:00:00Z",
				last_used_at: iso(HOUR),
			},
			{
				id: randomUUID(),
				owner: tenant,
				name: "n8n automation",
				key_prefix: `tk_${shreeTenantId.slice(0, 8)}_Qw3e...`,
				permission: "write",
				is_default: false,
				created_by: "admin key",
				created_at: "2026-09-25T10:00:00Z",
				last_used_at: null,
			},
		],
		sent: [],
		secrets: [],
		seeded: new Set([kb]),
	};
}

const view = ({ owner: _owner, ...k }: MockApiKey) => k;

export async function handleKeysRoute(
	s: KeysState,
	method: string,
	path: string,
	body: Record<string, unknown>,
	ctx: KeysCtx,
): Promise<boolean> {
	const m =
		/^\/api\/v1\/(?:corpora\/([^/]+)|tenants\/(me)|admin\/tenants\/([^/]+))\/keys(?:\/([^/]+))?(\/rotate)?$/.exec(path);
	if (!m) return false;
	const [, kbId, me, adminTenant, keyId, rotate] = m;
	const done = (status: number, payload?: unknown) => ctx.reply(status, payload).then(() => true);

	let owner: string;
	let kind: "ck" | "tk";
	if (kbId) {
		if (!ctx.canUseKb(kbId)) return done(403, { detail: "Not allowed to access this corpus" });
		owner = `kb:${kbId}`;
		kind = "ck";
	} else if (me) {
		if (!ctx.tenantId) {
			return done(403, { detail: "This route is for a tenant (a tk_ key or a workspace sign-in)" });
		}
		owner = `tenant:${ctx.tenantId}`;
		kind = "tk";
	} else {
		if (!ctx.isAdmin) return done(403, { detail: "Admin access required" });
		owner = `tenant:${adminTenant}`;
		kind = "tk";
	}
	if (ctx.role === "member") {
		return done(403, { detail: "Only a workspace owner can do this", code: "owner_required" });
	}
	const id8 = owner.split(":")[1]?.slice(0, 8) ?? "";
	// A knowledge base made after the mock started has the "Default key" its creation issued.
	if (kind === "ck" && !s.seeded.has(owner)) {
		s.seeded.add(owner);
		s.keys.push({
			id: randomUUID(),
			owner,
			name: "Default key",
			key_prefix: `ck_${id8}_...`,
			permission: "full",
			is_default: true,
			created_by: ctx.actor,
			created_at: new Date().toISOString(),
			last_used_at: null,
		});
	}
	const mine = () => s.keys.filter((k) => k.owner === owner);
	const find = () => mine().find((k) => k.id === keyId);
	const notFound = () => done(404, { detail: "API key not found", code: "key_not_found" });
	const secret = (tag: string) => `${kind}_${id8}_${tag}${s.secrets.length + 1}`;
	const log = () => s.sent.push({ method, path, body });

	if (!keyId && method === "GET") return done(200, { keys: mine().map(view) });

	if (!keyId && method === "POST") {
		log();
		const name = String(body.name ?? "").trim();
		if (!name) return done(422, { detail: "Give the key a name", code: "key_name_required" });
		const permission = (body.permission ?? "read") as MockPermission;
		if (!["read", "write", "full"].includes(permission)) {
			return done(422, { detail: "Permission must be read, write or full", code: "invalid_permission" });
		}
		const key: MockApiKey = {
			id: randomUUID(),
			owner,
			name,
			key_prefix: `${kind}_${id8}_N${s.keys.length}...`,
			permission,
			is_default: false,
			created_by: ctx.actor,
			created_at: new Date().toISOString(),
			last_used_at: null,
		};
		s.keys.push(key);
		const plaintext = secret("mockNewSecret");
		s.secrets.push({ keyId: key.id, secret: plaintext });
		return done(201, { ...view(key), api_key: plaintext });
	}

	if (keyId && method === "PATCH") {
		log();
		const k = find();
		if (!k) return notFound();
		if (typeof body.name === "string") k.name = body.name.trim() || k.name;
		if (typeof body.permission === "string") k.permission = body.permission as MockPermission;
		return done(200, view(k));
	}

	if (keyId && rotate && method === "POST") {
		log();
		const k = find();
		if (!k) return notFound();
		const plaintext = secret("mockRotatedSecret");
		s.secrets.push({ keyId: k.id, secret: plaintext });
		k.last_used_at = null;
		return done(200, { id: k.id, key_prefix: k.key_prefix, api_key: plaintext });
	}

	if (keyId && !rotate && method === "DELETE") {
		log();
		if (!find()) return notFound();
		s.keys = s.keys.filter((k) => k.id !== keyId);
		return done(204);
	}
	return false;
}
