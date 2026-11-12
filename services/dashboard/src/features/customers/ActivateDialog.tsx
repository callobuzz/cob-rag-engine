import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { assignablePlansQuery, plansQuery } from "@/features/plans/api";
import { planSummary } from "@/features/plans/limits";
import type { AdminTenant, Plan } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import type { ApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { customersStrings as t } from "@/lib/strings/accounts";
import { en, fill } from "@/lib/strings/en";
import { planChoiceStrings } from "@/lib/strings/plan-choice"; // P6 plan choice
import { useActivateTenant } from "./api";

export const NOTE_MAX = 1000;

/**
 * Plans for a picker, the default first: the assignable ones from `include_inactive=false` (no
 * switched-off plan, never the Internal plan). `all` is every plan, to name a customer's current
 * plan even when it is switched off; the query state is the assignable list's.
 */
export function useActivePlans() {
	const q = useQuery(assignablePlansQuery);
	const every = useQuery(plansQuery);
	const plans = q.data?.items ?? [];
	return {
		...q,
		all: every.data?.items ?? plans,
		plans: [...plans].sort((a, b) => Number(b.is_default) - Number(a.is_default)),
	};
}

export function PlanOptions({ plans, currentId }: { plans: Plan[]; currentId?: string }) {
	return (
		<>
			{plans.map((p) => (
				<option key={p.id} value={p.id}>
					{fill(t.activate.planOption, {
						name: p.id === currentId ? fill(t.changePlan.current, { name: p.name }) : p.name,
						summary: planSummary(p),
					})}
				</option>
			))}
		</>
	);
}

/** Refetch the lists when the tenant already moved on (409 invalid_status_transition). */
export function useStaleStatus() {
	const qc = useQueryClient();
	return (err: ApiError) => {
		if (err.code === "invalid_status_transition" || err.status === 404) {
			void qc.invalidateQueries({ queryKey: qk.tenants.all });
		}
	};
}

/**
 * Activate a pending sign-up (mockup `#activateDlg`): plan (active plans, the default
 * preselected) and an optional note (≤ 1000, only the operator sees it) → `POST …/activate
 * {plan_id, note}`. No "email the owner" checkbox: the engine always mails the owners (A4).
 */
export function ActivateDialog({
	tenant,
	onOpenChange,
}: {
	tenant: AdminTenant | null;
	onOpenChange: (open: boolean) => void;
}) {
	return (
		<Dialog open={tenant !== null} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[520px]">
				{tenant ? <ActivateForm tenant={tenant} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

const activateSchema = z.object({
	plan: z.string().min(1, "Pick a plan."),
	note: z.string().max(NOTE_MAX, t.activate.noteLong),
});
type ActivateValues = z.infer<typeof activateSchema>;

function ActivateForm({ tenant, onDone }: { tenant: AdminTenant; onDone: () => void }) {
	const ids = { plan: useId(), note: useId() };
	const { plans, isPending, isError, refetch } = useActivePlans();
	const activate = useActivateTenant();
	const stale = useStaleStatus();
	const form = useAppForm<ActivateValues>({
		resolver: zodResolver(activateSchema),
		defaultValues: { plan: "", note: "" },
	});
	const errs = form.formState.errors;
	// P6 plan choice: the plan the owner chose at sign-up comes first when it can still be given
	const requested = plans.some((p) => p.id === tenant.requested_plan_id) ? tenant.requested_plan_id : null;
	const defaultId = requested ?? plans[0]?.id ?? "";
	const { getValues, setValue } = form;
	useEffect(() => {
		if (!getValues("plan") && defaultId) setValue("plan", defaultId);
	}, [defaultId, getValues, setValue]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const r = await activate.mutateAsync({ id: tenant.id, plan_id: v.plan, note: v.note.trim() || null });
			toast.success(fill(t.activate.toast, { name: tenant.name }), {
				description: fill(t.activate.toastBody, { plan: r.plan_name }),
			});
			onDone();
		},
		{
			fieldName: (path) => (path === "note" ? "note" : path === "plan_id" ? "plan" : null),
			onError: (err) => {
				stale(err);
				if (err.code === "plan_inactive" || err.code === "plan_not_found" || err.code === "plan_not_assignable") {
					form.setError("plan", { type: "server", message: messageFor(err).title });
					form.setFocus("plan");
					void refetch();
					return true;
				}
				return false;
			},
		},
	);

	const owner = tenant.owner_emails[0] ?? t.activate.ownerFallback;
	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{fill(t.activate.title, { name: tenant.name })}</DialogTitle>
				<DialogDescription>{fill(t.activate.lead, { owner })}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
			{isError ? (
				<FormAlert>
					{t.activate.plansError}{" "}
					<button type="button" className="font-semibold underline" onClick={() => void refetch()}>
						{en.common.retry}
					</button>
				</FormAlert>
			) : !isPending && plans.length === 0 ? (
				<FormAlert tone="warn">{t.activate.noPlans}</FormAlert>
			) : null}
			<Field
				id={ids.plan}
				label={t.activate.plan}
				hint={
					requested ? fill(planChoiceStrings.admin.requested, { plan: tenant.requested_plan_name ?? "" }) : undefined
				}
				error={errs.plan?.message}
			>
				<NativeSelect
					id={ids.plan}
					disabled={isPending || plans.length === 0}
					aria-invalid={!!errs.plan || undefined}
					aria-describedby={describedBy(ids.plan, { hint: !!requested, error: !!errs.plan })}
					{...form.register("plan")}
				>
					<PlanOptions plans={plans} />
				</NativeSelect>
			</Field>
			<Field id={ids.note} label={t.activate.note} hint={t.activate.noteHint} error={errs.note?.message}>
				<Input
					id={ids.note}
					autoComplete="off"
					placeholder={t.activate.notePlaceholder}
					aria-invalid={!!errs.note || undefined}
					aria-describedby={describedBy(ids.note, { hint: true, error: !!errs.note })}
					{...form.register("note")}
				/>
			</Field>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.activate.submitting}
				>
					{t.activate.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
