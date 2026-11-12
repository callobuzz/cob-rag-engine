import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import type { components } from "@/lib/api/schema";
import { RECENT_USE_MS } from "@/lib/strings/keys";

/**
 * Named API keys: many `ck_` keys per knowledge base and many `tk_` keys per customer, each with
 * a permission. The same five operations on three routes:
 *  - a knowledge base: `/corpora/{id}/keys`;
 *  - the caller's own workspace (a workspace owner's sign-in or a `tk_` key): `/tenants/me/keys`;
 *  - any customer, for the operator: `/admin/tenants/{id}/keys`.
 * A key is returned once, by create and rotate; a list holds only its prefix.
 */
type S = components["schemas"];
export type ApiKey = S["ApiKeyResponse"];
export type CreatedKey = S["CreateApiKeyResponse"];
export type RotatedKey = S["RotateApiKeyResponse"];
export type KeyPermission = ApiKey["permission"];

export const KEY_PERMISSIONS: readonly KeyPermission[] = ["read", "write", "full"];

export type KeyOwner =
	| { kind: "kb"; id: string; name: string }
	| { kind: "workspace"; name: string }
	| { kind: "customer"; id: string; name: string };

export function ownerKey(owner: KeyOwner): string {
	return owner.kind === "workspace" ? "workspace" : `${owner.kind}:${owner.id}`;
}

/** True when the key was used within the last 24 hours (a risky action then gets a warning). */
export function usedRecently(key: Pick<ApiKey, "last_used_at">, now: number = Date.now()): boolean {
	if (!key.last_used_at) return false;
	const at = new Date(key.last_used_at).getTime();
	return !Number.isNaN(at) && now - at < RECENT_USE_MS;
}

function listKeys(owner: KeyOwner) {
	switch (owner.kind) {
		case "kb":
			return api().GET("/api/v1/corpora/{corpus_id}/keys", { params: { path: { corpus_id: owner.id } } });
		case "workspace":
			return api().GET("/api/v1/tenants/me/keys");
		case "customer":
			return api().GET("/api/v1/admin/tenants/{tenant_id}/keys", { params: { path: { tenant_id: owner.id } } });
	}
}

export function keysQuery(owner: KeyOwner) {
	return queryOptions({
		queryKey: qk.apiKeys.byOwner(ownerKey(owner)),
		queryFn: async () => (await unwrap(listKeys(owner))).keys,
	});
}

export function createKey(owner: KeyOwner, body: { name: string; permission: KeyPermission }): Promise<CreatedKey> {
	switch (owner.kind) {
		case "kb":
			return unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/keys", { params: { path: { corpus_id: owner.id } }, body }),
			);
		case "workspace":
			return unwrap(api().POST("/api/v1/tenants/me/keys", { body }));
		case "customer":
			return unwrap(
				api().POST("/api/v1/admin/tenants/{tenant_id}/keys", { params: { path: { tenant_id: owner.id } }, body }),
			);
	}
}

export function updateKey(
	owner: KeyOwner,
	keyId: string,
	body: { name?: string; permission?: KeyPermission },
): Promise<ApiKey> {
	switch (owner.kind) {
		case "kb":
			return unwrap(
				api().PATCH("/api/v1/corpora/{corpus_id}/keys/{key_id}", {
					params: { path: { corpus_id: owner.id, key_id: keyId } },
					body,
				}),
			);
		case "workspace":
			return unwrap(api().PATCH("/api/v1/tenants/me/keys/{key_id}", { params: { path: { key_id: keyId } }, body }));
		case "customer":
			return unwrap(
				api().PATCH("/api/v1/admin/tenants/{tenant_id}/keys/{key_id}", {
					params: { path: { tenant_id: owner.id, key_id: keyId } },
					body,
				}),
			);
	}
}

export function rotateKey(owner: KeyOwner, keyId: string): Promise<RotatedKey> {
	switch (owner.kind) {
		case "kb":
			return unwrap(
				api().POST("/api/v1/corpora/{corpus_id}/keys/{key_id}/rotate", {
					params: { path: { corpus_id: owner.id, key_id: keyId } },
				}),
			);
		case "workspace":
			return unwrap(api().POST("/api/v1/tenants/me/keys/{key_id}/rotate", { params: { path: { key_id: keyId } } }));
		case "customer":
			return unwrap(
				api().POST("/api/v1/admin/tenants/{tenant_id}/keys/{key_id}/rotate", {
					params: { path: { tenant_id: owner.id, key_id: keyId } },
				}),
			);
	}
}

export function revokeKey(owner: KeyOwner, keyId: string): Promise<unknown> {
	switch (owner.kind) {
		case "kb":
			return unwrap(
				api().DELETE("/api/v1/corpora/{corpus_id}/keys/{key_id}", {
					params: { path: { corpus_id: owner.id, key_id: keyId } },
				}),
			);
		case "workspace":
			return unwrap(api().DELETE("/api/v1/tenants/me/keys/{key_id}", { params: { path: { key_id: keyId } } }));
		case "customer":
			return unwrap(
				api().DELETE("/api/v1/admin/tenants/{tenant_id}/keys/{key_id}", {
					params: { path: { tenant_id: owner.id, key_id: keyId } },
				}),
			);
	}
}

/** What a key change makes stale: its own list, and the customer's "has a key" flag. */
function useInvalidateKeys(owner: KeyOwner) {
	const qc = useQueryClient();
	return () =>
		Promise.all([
			qc.invalidateQueries({ queryKey: qk.apiKeys.byOwner(ownerKey(owner)) }),
			qc.invalidateQueries({ queryKey: qk.tenants.all }),
		]);
}

export function useCreateKey(owner: KeyOwner) {
	const invalidate = useInvalidateKeys(owner);
	return useMutation({
		mutationFn: (body: { name: string; permission: KeyPermission }) => createKey(owner, body),
		onSuccess: invalidate,
	});
}

export function useUpdateKey(owner: KeyOwner) {
	const invalidate = useInvalidateKeys(owner);
	return useMutation({
		mutationFn: (v: { id: string; name?: string; permission?: KeyPermission }) =>
			updateKey(owner, v.id, { name: v.name, permission: v.permission }),
		onSuccess: invalidate,
	});
}

export function useRotateKey(owner: KeyOwner) {
	const invalidate = useInvalidateKeys(owner);
	return useMutation({ mutationFn: (id: string) => rotateKey(owner, id), onSuccess: invalidate });
}

export function useRevokeKey(owner: KeyOwner) {
	const invalidate = useInvalidateKeys(owner);
	return useMutation({ mutationFn: (id: string) => revokeKey(owner, id), onSuccess: invalidate });
}

/**
 * Create a key for an owner chosen at submit time (the Agents page picks a knowledge base or its
 * customer with a select), then refresh that owner's list.
 */
export function useCreateKeyFor() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (v: { owner: KeyOwner; name: string; permission: KeyPermission }) =>
			createKey(v.owner, { name: v.name, permission: v.permission }),
		onSuccess: (_key, v) =>
			Promise.all([
				qc.invalidateQueries({ queryKey: qk.apiKeys.byOwner(ownerKey(v.owner)) }),
				qc.invalidateQueries({ queryKey: qk.tenants.all }),
			]),
	});
}
