import { useMutation, useQueryClient } from "@tanstack/react-query";
import { authFetch } from "@/lib/api/auth-fetch";
import { parseApiError } from "@/lib/api/errors";
import type { components } from "@/lib/api/schema";
import { currentRagUrl } from "@/lib/auth/store";

/**
 * Confirming or dismissing an action card (COB5): `POST /api/v1/cob/actions/{id}/confirm|cancel`.
 * The engine runs the same route the dashboard would, with this person's own permissions, and
 * answers 200 with `status: "failed"` when that route refused. Only a card that cannot run at all
 * (expired, used, another workspace, wrong typed name) is an error here.
 */
export type CobActionResult = components["schemas"]["CobActionResult"];

async function post(path: string, body?: unknown): Promise<Response> {
	const response = await authFetch(`${currentRagUrl()}${path}`, {
		method: "POST",
		headers: { "Content-Type": "application/json", Accept: "application/json" },
		body: JSON.stringify(body ?? {}),
	});
	if (!response.ok) throw await parseApiError(response);
	return response;
}

export async function confirmCobAction(id: string, confirmName: string | null): Promise<CobActionResult> {
	const response = await post(
		`/api/v1/cob/actions/${id}/confirm`,
		confirmName == null ? {} : { confirm_name: confirmName },
	);
	return (await response.json()) as CobActionResult;
}

export async function cancelCobAction(id: string): Promise<void> {
	await post(`/api/v1/cob/actions/${id}/cancel`);
}

/** What a confirmed action may have changed is anywhere on screen: refetch it all once it ran. */
export function useConfirmCobAction() {
	const qc = useQueryClient();
	return useMutation({
		mutationFn: (v: { id: string; confirmName: string | null }) => confirmCobAction(v.id, v.confirmName),
		onSuccess: (result) => {
			if (result.status === "done") void qc.invalidateQueries();
		},
	});
}

export function useCancelCobAction() {
	return useMutation({ mutationFn: (id: string) => cancelCobAction(id) });
}
