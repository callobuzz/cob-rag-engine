import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { MemberRole } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";

/**
 * Teams (A5). Two scopes share the screens: `me` (the signed-in workspace: `/tenants/me/...`,
 * owners only for writes) and `admin` (the operator on any tenant: `/admin/tenants/{id}/...`,
 * where invitations carry `accept_url`). Team actions invalidate the team (members +
 * invitations) and, for the admin, the customer (member count, owner e-mails).
 */
export type TeamScope = { kind: "me" } | { kind: "admin"; tenantId: string };

export function teamKey(scope: TeamScope) {
	return scope.kind === "me" ? qk.team : qk.tenants.team(scope.tenantId);
}

export function teamQuery(scope: TeamScope) {
	return queryOptions({
		queryKey: teamKey(scope),
		queryFn: () =>
			scope.kind === "me"
				? unwrap(api().GET("/api/v1/tenants/me/members"))
				: unwrap(
						api().GET("/api/v1/admin/tenants/{tenant_id}/members", {
							params: { path: { tenant_id: scope.tenantId } },
						}),
					),
	});
}

function useInvalidate(scope: TeamScope) {
	const qc = useQueryClient();
	return () =>
		Promise.all([
			qc.invalidateQueries({ queryKey: teamKey(scope) }),
			scope.kind === "admin" ? qc.invalidateQueries({ queryKey: qk.tenants.all }) : null,
		]);
}

export function useInvite(scope: TeamScope) {
	const invalidate = useInvalidate(scope);
	return useMutation({
		mutationFn: async (body: { email: string; role: MemberRole }) => {
			if (scope.kind === "me") {
				const inv = await unwrap(api().POST("/api/v1/tenants/me/members", { body }));
				return { ...inv, accept_url: null as string | null };
			}
			return unwrap(
				api().POST("/api/v1/admin/tenants/{tenant_id}/invitations", {
					params: { path: { tenant_id: scope.tenantId } },
					body,
				}),
			);
		},
		onSuccess: () => invalidate(),
	});
}

/**
 * A new link and 7 more days. `me`: e-mailed (owners). `admin` (A8): the response carries the new
 * `accept_url`, e-mailed too when the engine has SMTP.
 */
export function useResendInvitation(scope: TeamScope) {
	const invalidate = useInvalidate(scope);
	return useMutation({
		mutationFn: async (id: string) => {
			if (scope.kind === "me") {
				const inv = await unwrap(
					api().POST("/api/v1/tenants/me/invitations/{invitation_id}/resend", {
						params: { path: { invitation_id: id } },
					}),
				);
				return { ...inv, accept_url: null as string | null };
			}
			return unwrap(
				api().POST("/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}/resend", {
					params: { path: { tenant_id: scope.tenantId, invitation_id: id } },
				}),
			);
		},
		onSuccess: () => invalidate(),
	});
}

export function useRevokeInvitation(scope: TeamScope) {
	const invalidate = useInvalidate(scope);
	return useMutation({
		mutationFn: (id: string) =>
			scope.kind === "me"
				? unwrap(
						api().DELETE("/api/v1/tenants/me/invitations/{invitation_id}", {
							params: { path: { invitation_id: id } },
						}),
					)
				: unwrap(
						api().DELETE("/api/v1/admin/tenants/{tenant_id}/invitations/{invitation_id}", {
							params: { path: { tenant_id: scope.tenantId, invitation_id: id } },
						}),
					),
		onSuccess: () => invalidate(),
	});
}

export function useChangeRole(scope: TeamScope) {
	const invalidate = useInvalidate(scope);
	return useMutation({
		mutationFn: ({ userId, role }: { userId: string; role: MemberRole }) =>
			scope.kind === "me"
				? unwrap(
						api().PATCH("/api/v1/tenants/me/members/{user_id}", {
							params: { path: { user_id: userId } },
							body: { role },
						}),
					)
				: unwrap(
						api().PATCH("/api/v1/admin/tenants/{tenant_id}/members/{user_id}", {
							params: { path: { tenant_id: scope.tenantId, user_id: userId } },
							body: { role },
						}),
					),
		onSuccess: () => invalidate(),
	});
}

/** Remove someone (owners, the admin) or leave (anyone, with their own id). */
export function useRemoveMember(scope: TeamScope) {
	const invalidate = useInvalidate(scope);
	return useMutation({
		mutationFn: (userId: string) =>
			scope.kind === "me"
				? unwrap(api().DELETE("/api/v1/tenants/me/members/{user_id}", { params: { path: { user_id: userId } } }))
				: unwrap(
						api().DELETE("/api/v1/admin/tenants/{tenant_id}/members/{user_id}", {
							params: { path: { tenant_id: scope.tenantId, user_id: userId } },
						}),
					),
		onSuccess: () => invalidate(),
	});
}

/**
 * Leave the signed-in workspace. The engine ends every session acting in it (this one too), so
 * nothing is refetched: the caller signs out locally at once (a refetch would meet
 * `session_revoked`).
 */
export function useLeaveWorkspace() {
	return useMutation({
		mutationFn: (userId: string) =>
			unwrap(api().DELETE("/api/v1/tenants/me/members/{user_id}", { params: { path: { user_id: userId } } })),
	});
}
