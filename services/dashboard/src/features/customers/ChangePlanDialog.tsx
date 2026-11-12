import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId, useState } from "react";
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
import {
	AgreedField,
	type Agreement,
	agreementBody,
	agreementValid,
	NOT_AGREED,
} from "@/features/billing/accounts/AgreedField"; // TV3 notice
import type { AdminTenant } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { formatDate } from "@/lib/format/date"; // TV3 notice
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { customersStrings as t } from "@/lib/strings/accounts";
import { en, fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises"; // TV3 notice
import { PlanOptions, useActivePlans, useStaleStatus } from "./ActivateDialog";
import { useSetTenantPlan } from "./api";

/**
 * Change a customer's plan → `POST /admin/tenants/{id}/plan {plan_id}` (A6; no note: the
 * endpoint takes none). The current plan is preselected and marked "(current)".
 */
export function ChangePlanDialog({
	tenant,
	onOpenChange,
}: {
	tenant: AdminTenant | null;
	onOpenChange: (open: boolean) => void;
}) {
	return (
		<Dialog open={tenant !== null} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[520px]">
				{tenant ? <ChangePlanForm tenant={tenant} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function ChangePlanForm({ tenant, onDone }: { tenant: AdminTenant; onDone: () => void }) {
	const id = useId();
	const { plans, all, isPending, refetch } = useActivePlans();
	const setPlan = useSetTenantPlan();
	const stale = useStaleStatus();
	const [agreement, setAgreement] = useState<Agreement>(NOT_AGREED); // TV3 notice
	const [showReasonError, setShowReasonError] = useState(false); // TV3 notice
	const [schema] = useState(() =>
		z.object({ plan: z.string().refine((p) => p !== tenant.plan_id, "Pick a different plan.") }),
	);
	const form = useAppForm<{ plan: string }>({
		resolver: zodResolver(schema),
		defaultValues: { plan: tenant.plan_id },
	});
	const err = form.formState.errors.plan?.message;
	// A switched-off current plan is still shown, so the select never lies about what they have.
	const current = all.find((p) => p.id === tenant.plan_id);
	const options = current && !current.is_active ? [current, ...plans] : plans;

	// The options arrive after the form mounts; a select cannot hold a value it has no option for yet.
	const { getFieldState, setValue } = form;
	const optionCount = options.length;
	useEffect(() => {
		if (optionCount > 0 && !getFieldState("plan").isDirty) setValue("plan", tenant.plan_id);
	}, [optionCount, getFieldState, setValue, tenant.plan_id]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			// TV3 notice: a move to a dearer plan waits for the notice period unless the business asked
			setShowReasonError(true);
			if (!agreementValid(agreement)) return;
			const r = await setPlan.mutateAsync({ id: tenant.id, plan_id: v.plan, ...agreementBody(agreement) });
			if (r.plan_change_scheduled_for) {
				toast.success(noSurprisesStrings.pending.scheduledPlanToast, {
					description: fill(noSurprisesStrings.pending.scheduledToastBody, {
						name: tenant.name,
						date: formatDate(r.plan_change_scheduled_for),
					}),
				});
				onDone();
				return;
			}
			// /TV3 notice
			toast.success(t.changePlan.toast, {
				description: fill(t.changePlan.toastBody, { name: tenant.name, plan: r.plan_name }),
			});
			onDone();
		},
		{
			onError: (e) => {
				stale(e);
				if (e.code === "plan_inactive" || e.code === "plan_not_found" || e.code === "plan_not_assignable") {
					form.setError("plan", { type: "server", message: messageFor(e).title });
					form.setFocus("plan");
					void refetch();
					return true;
				}
				return false;
			},
		},
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{fill(t.changePlan.title, { name: tenant.name })}</DialogTitle>
				<DialogDescription>{t.changePlan.lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
			<Field id={id} label={t.activate.plan} error={err}>
				<NativeSelect
					id={id}
					disabled={isPending}
					aria-invalid={!!err || undefined}
					aria-describedby={describedBy(id, { error: !!err })}
					{...form.register("plan")}
				>
					<PlanOptions plans={options} currentId={tenant.plan_id} />
				</NativeSelect>
			</Field>
			<AgreedField value={agreement} onChange={setAgreement} showError={showReasonError} /> {/* TV3 notice */}
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
					{t.changePlan.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
