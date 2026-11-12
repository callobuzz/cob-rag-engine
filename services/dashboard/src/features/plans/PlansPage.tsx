import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import type { Plan } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { plansStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { plansQuery, useDeletePlan, useUpdatePlan } from "./api";
import { builtInPlanId, sortPlans } from "./limits";
import { type PlanAction, PlanCard } from "./PlanCard";
import { PlanDialog } from "./PlanDialog";

/** `/admin/plans` (mockup `[data-view=plans]`). */
export function PlansPage() {
	const plans = useQuery(plansQuery);
	const update = useUpdatePlan();
	const del = useDeletePlan();
	const [editing, setEditing] = useState<{ plan: Plan | null } | null>(null);
	const [deleting, setDeleting] = useState<Plan | null>(null);
	const [deleteError, setDeleteError] = useState<string | null>(null);

	const toastError = (e: unknown) => {
		const msg = messageFor(toApiError(e));
		toast.error(msg.title, msg.body ? { description: msg.body } : undefined);
	};

	const onAction = async (action: PlanAction, plan: Plan) => {
		if (action === "edit") return setEditing({ plan });
		if (action === "delete") {
			setDeleteError(null);
			return setDeleting(plan);
		}
		try {
			if (action === "default") {
				await update.mutateAsync({ id: plan.id, body: { is_default: true } });
				toast.success(fill(t.card.defaultToast, { name: plan.name }), { description: t.card.defaultToastBody });
			} else {
				const on = !plan.is_active;
				await update.mutateAsync({ id: plan.id, body: { is_active: on } });
				toast.success(fill(on ? t.card.onToast : t.card.offToast, { name: plan.name }), {
					description: on ? t.card.onToastBody : t.card.offToastBody,
				});
			}
		} catch (e) {
			toastError(e);
		}
	};

	const doDelete = async () => {
		if (!deleting) return;
		try {
			await del.mutateAsync(deleting.id);
			toast.success(fill(t.remove.toast, { name: deleting.name }));
			setDeleting(null);
		} catch (e) {
			setDeleteError(messageFor(toApiError(e)).title);
		}
	};

	const items = plans.data ? sortPlans(plans.data.items) : [];
	const builtIn = plans.data ? builtInPlanId(plans.data.items) : null;

	return (
		<>
			<PageHeader
				title={t.page.title}
				description={t.page.lead}
				actions={
					<Button variant="default" onClick={() => setEditing({ plan: null })}>
						<Plus aria-hidden />
						{t.page.newPlan}
					</Button>
				}
			/>
			{plans.isPending ? (
				<div className="grid gap-4 min-[861px]:grid-cols-3" aria-busy="true">
					<CardSkeleton lines={5} />
					<CardSkeleton lines={5} />
					<CardSkeleton lines={5} />
				</div>
			) : plans.isError ? (
				<ErrorState error={plans.error} onRetry={() => void plans.refetch()} />
			) : (
				<section aria-label={t.page.listLabel} className="grid gap-4 min-[861px]:grid-cols-2 min-[1101px]:grid-cols-3">
					{items.map((p) => (
						<PlanCard key={p.id} plan={p} builtIn={p.id === builtIn} onAction={(a, x) => void onAction(a, x)} />
					))}
				</section>
			)}
			<FormAlert tone="info">{t.page.enforcement}</FormAlert>
			<PlanDialog
				open={editing !== null}
				onOpenChange={(o) => (o ? undefined : setEditing(null))}
				plan={editing?.plan ?? null}
			/>
			<ConfirmDialog
				open={deleting !== null}
				onOpenChange={(o) => (o ? undefined : setDeleting(null))}
				title={fill(t.remove.title, { name: deleting?.name ?? "" })}
				body={t.remove.body}
				confirmLabel={t.remove.confirm}
				pending={del.isPending}
				onConfirm={() => void doDelete()}
			>
				{deleteError ? <FormAlert>{deleteError}</FormAlert> : null}
			</ConfirmDialog>
		</>
	);
}
