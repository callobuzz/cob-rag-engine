import { queryOptions, useMutation, useQueryClient } from "@tanstack/react-query";
import type { MeResponse } from "@/lib/api/accounts-types";
import { api, unwrap } from "@/lib/api/client";
import { qk } from "@/lib/api/query-keys";
import { getAuthState, setAuthState } from "@/lib/auth/store";

/**
 * Profile and sessions (A3; email sign-ins only — an API key gets 403 `session_required`, so the
 * page never calls these for a key principal). Profile → `qk.me` (and the principal's name);
 * password and sessions → the sessions list.
 */
export const meQuery = queryOptions({
	queryKey: qk.me,
	queryFn: () => unwrap(api().GET("/api/v1/auth/me")),
});

export const sessionsQuery = queryOptions({
	queryKey: qk.sessions,
	queryFn: () => unwrap(api().GET("/api/v1/auth/sessions")),
});

export function useUpdateProfile() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (name: string) => unwrap(api().PATCH("/api/v1/auth/me", { body: { name } })),
		onSuccess: (me: MeResponse) => {
			qc.setQueryData(qk.me, me);
			const p = getAuthState().principal;
			if (p?.user) setAuthState({ principal: { ...p, user: { ...p.user, name: me.user.name } } });
		},
	});
}

export function useChangePassword() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (body: { current_password: string; new_password: string }) =>
			unwrap(api().POST("/api/v1/auth/change-password", { body })),
		onSuccess: () => qc.invalidateQueries({ queryKey: qk.sessions }),
	});
}

export function useRevokeSession() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (id: string) =>
			unwrap(api().DELETE("/api/v1/auth/sessions/{session_id}", { params: { path: { session_id: id } } })),
		onSuccess: () => qc.invalidateQueries({ queryKey: qk.sessions }),
	});
}

export function useRevokeOtherSessions() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: () => unwrap(api().POST("/api/v1/auth/sessions/revoke-others")),
		onSuccess: () => qc.invalidateQueries({ queryKey: qk.sessions }),
	});
}
