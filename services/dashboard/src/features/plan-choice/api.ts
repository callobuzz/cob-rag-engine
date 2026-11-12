import { queryOptions, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";

/**
 * Choosing and changing a plan (P6): `GET /billing/plans` (owners: the plans the workspace may
 * move to, each with what a move costs now), `POST /billing/plan` (move), `DELETE
 * /billing/plan/pending` (call off a scheduled or waiting move), and the public list the sign-up
 * page reads (`GET /public/plans`, no credential). Paying for a waiting move is the pay page with
 * a top-up (`/pay?purpose=top_up&amount=...`), never a call from here.
 */
export const planChoiceKeys = {
	list: ["plan-choice", "list"] as const,
	public: ["plan-choice", "public"] as const,
};

export const planChoicesQuery = queryOptions({
	queryKey: planChoiceKeys.list,
	queryFn: () => unwrap(api().GET("/api/v1/billing/plans")),
	retry: false,
	staleTime: 30_000,
});

export const publicPlansQuery = queryOptions({
	queryKey: planChoiceKeys.public,
	queryFn: () => unwrap(api().GET("/api/v1/public/plans")),
	retry: false,
	staleTime: 300_000,
});

export function usePublicPlans(enabled: boolean) {
	return useQuery({ ...publicPlansQuery, enabled });
}

/** What a move changes: the plan list, the plan and its limits, the balance. */
function useRefresh() {
	const qc = useQueryClient();
	return () => {
		void qc.invalidateQueries({ queryKey: ["plan-choice"] });
		void qc.invalidateQueries({ queryKey: ["billing"] });
		void qc.invalidateQueries({ queryKey: ["tenants"] });
		void qc.invalidateQueries({ queryKey: ["plan"] });
		void qc.invalidateQueries({ queryKey: ["shop"] });
	};
}

export function useChangePlan() {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: (planId: string) => unwrap(api().POST("/api/v1/billing/plan", { body: { plan_id: planId } })),
		onSuccess: refresh,
	});
}

export function useCancelPlanChange() {
	const refresh = useRefresh();
	return useMutation({
		mutationFn: () => unwrap(api().DELETE("/api/v1/billing/plan/pending")),
		onSuccess: refresh,
	});
}

/** `/signup?plan=...` names a plan by its slug or id; the public list resolves it (case-blind). */
export function findPlan<T extends { id: string; slug: string }>(items: T[], ref: string | undefined): T | null {
	const wanted = (ref ?? "").trim().toLowerCase();
	if (!wanted) return null;
	return items.find((p) => p.id.toLowerCase() === wanted || p.slug === wanted) ?? null;
}

/** Minor units as the plain decimal a money field takes ("4999.01"), with no float. */
export function majorText(minor: number): string {
	return `${Math.trunc(minor / 100)}.${String(minor % 100).padStart(2, "0")}`;
}
