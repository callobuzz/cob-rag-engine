import { zodResolver } from "@hookform/resolvers/zod";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { SecretReveal } from "@/components/patterns/SecretReveal";
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
import type { CreateTenantResponse } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { customersStrings as t } from "@/lib/strings/accounts";
import { en, fill } from "@/lib/strings/en";
import { PlanOptions, useActivePlans } from "./ActivateDialog";
import { useCreateTenant } from "./api";
import { AdminInviteForm } from "./InviteOwnerStep";

/** "", or a whole number 1–10000 → the override; anything else → undefined (invalid). */
export function parseKbLimit(text: string): number | null | undefined {
	const s = text.trim();
	if (s === "") return null;
	if (!/^\d+$/.test(s)) return undefined;
	const n = Number(s);
	return n >= 1 && n <= 10000 ? n : undefined;
}

type Step =
	| { kind: "form" }
	| { kind: "key"; tenant: CreateTenantResponse }
	| { kind: "invite"; tenant: CreateTenantResponse };

/**
 * Add a customer by hand (D7): name, description, plan (the default preselected), KB-limit
 * override → `POST /admin/tenants` → its `tk_` key once → "Invite the owner" (admin invitation
 * with `accept_url`, so it works without SMTP and in Closed mode).
 */
export function AddCustomerDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
	const [step, setStep] = useState<Step>({ kind: "form" });
	const navigate = useNavigate();
	const close = (o: boolean) => {
		onOpenChange(o);
		if (!o) setStep({ kind: "form" });
	};
	const openCustomer = (id: string) => {
		close(false);
		void navigate({ to: "/admin/customers/$tenantId", params: { tenantId: id } });
	};
	return (
		<Dialog open={open} onOpenChange={close}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-xl">
				{!open ? null : step.kind === "form" ? (
					<AddForm onCreated={(tenant) => setStep({ kind: "key", tenant })} onCancel={() => close(false)} />
				) : step.kind === "key" ? (
					<div className="grid gap-4">
						<DialogHeader>
							<DialogTitle>{fill(t.add.createdTitle, { name: step.tenant.name })}</DialogTitle>
							<DialogDescription>{t.add.createdLead}</DialogDescription>
						</DialogHeader>
						<SecretReveal value={step.tenant.api_key} label={t.add.keyLabel} />
						<DialogFooter>
							<Button type="button" variant="ghost" onClick={() => close(false)}>
								{t.invite.skip}
							</Button>
							<Button type="button" variant="default" onClick={() => setStep({ kind: "invite", tenant: step.tenant })}>
								{t.add.next}
							</Button>
						</DialogFooter>
					</div>
				) : (
					<AdminInviteForm
						tenant={step.tenant}
						fixedRole="owner"
						title={fill(t.invite.title, { name: step.tenant.name })}
						lead={t.invite.lead}
						secondary={
							<Button type="button" variant="ghost" onClick={() => close(false)}>
								{t.invite.skip}
							</Button>
						}
						after={() => (
							<>
								<Button type="button" variant="ghost" onClick={() => close(false)}>
									{t.invite.done}
								</Button>
								<Button type="button" variant="default" onClick={() => openCustomer(step.tenant.id)}>
									{t.invite.open}
								</Button>
							</>
						)}
					/>
				)}
			</DialogContent>
		</Dialog>
	);
}

const addSchema = z.object({
	name: z.string().trim().min(1, t.add.nameRequired).max(255, t.add.nameLong),
	description: z.string(),
	plan: z.string(),
	limit: z.string().refine((v) => parseKbLimit(v) !== undefined, t.add.kbLimitRange),
});
type AddValues = z.infer<typeof addSchema>;

function AddForm({ onCreated, onCancel }: { onCreated: (t: CreateTenantResponse) => void; onCancel: () => void }) {
	const ids = { name: useId(), description: useId(), plan: useId(), limit: useId() };
	const { plans, all, isPending } = useActivePlans();
	const create = useCreateTenant();
	const form = useAppForm<AddValues>({
		resolver: zodResolver(addSchema),
		defaultValues: { name: "", description: "", plan: "", limit: "" },
	});
	const errs = form.formState.errors;
	const { getValues, setValue, setFocus } = form;
	useEffect(() => {
		if (!getValues("plan") && plans[0]) setValue("plan", plans[0].id);
	}, [plans, getValues, setValue]);
	useEffect(() => {
		setFocus("name");
	}, [setFocus]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const r = await create.mutateAsync({
				name: v.name.trim(),
				description: v.description.trim() || null,
				plan_id: v.plan || null,
				max_corpora: parseKbLimit(v.limit) ?? null,
			});
			const plan = all.find((p) => p.id === r.plan_id)?.name ?? "";
			toast.success(t.add.toast, { description: fill(t.add.toastBody, { name: r.name, plan }) });
			onCreated(r);
		},
		{
			fieldName: (path) => (path === "name" ? "name" : path === "max_corpora" ? "limit" : null),
			onError: (err) => {
				if (err.code === "plan_inactive" || err.code === "plan_not_found" || err.code === "plan_not_assignable") {
					form.setError("plan", { type: "server", message: messageFor(err).title });
					form.setFocus("plan");
					return true;
				}
				return false;
			},
		},
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{t.add.title}</DialogTitle>
				<DialogDescription>{t.add.lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
			<Field id={ids.name} label={t.add.name} error={errs.name?.message}>
				<Input
					id={ids.name}
					autoComplete="off"
					placeholder={t.add.namePlaceholder}
					aria-invalid={!!errs.name || undefined}
					aria-describedby={describedBy(ids.name, { error: !!errs.name })}
					{...form.register("name")}
				/>
			</Field>
			<Field id={ids.description} label={t.add.description} hint={t.add.descriptionHint}>
				<Input
					id={ids.description}
					autoComplete="off"
					aria-describedby={describedBy(ids.description, { hint: true })}
					{...form.register("description")}
				/>
			</Field>
			<div className="grid gap-4 min-[600px]:grid-cols-2">
				<Field id={ids.plan} label={t.add.plan} error={errs.plan?.message}>
					<NativeSelect
						id={ids.plan}
						disabled={isPending}
						aria-invalid={!!errs.plan || undefined}
						aria-describedby={describedBy(ids.plan, { error: !!errs.plan })}
						{...form.register("plan")}
					>
						<PlanOptions plans={plans} />
					</NativeSelect>
				</Field>
				<Field id={ids.limit} label={t.add.kbLimit} hint={t.add.kbLimitHint} error={errs.limit?.message}>
					<Input
						id={ids.limit}
						inputMode="numeric"
						autoComplete="off"
						className="font-mono"
						aria-invalid={!!errs.limit || undefined}
						aria-describedby={describedBy(ids.limit, { hint: true, error: !!errs.limit })}
						{...form.register("limit")}
					/>
				</Field>
			</div>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onCancel}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.add.submitting}
				>
					{t.add.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
