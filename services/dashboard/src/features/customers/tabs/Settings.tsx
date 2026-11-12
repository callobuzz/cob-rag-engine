import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { plansQuery } from "@/features/plans/api";
import { formatLimit } from "@/features/plans/limits";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { customersStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { parseKbLimit } from "../AddCustomerDialog";
import { useUpdateTenant } from "../api";

const settingsSchema = z
	.object({
		name: z.string().trim().min(1, t.add.nameRequired).max(255, t.add.nameLong),
		description: z.string(),
		mode: z.enum(["plan", "custom"]),
		limit: z.string(),
	})
	.superRefine((v, ctx) => {
		if (v.mode !== "custom") return;
		const n = parseKbLimit(v.limit);
		if (n === undefined || n === null) ctx.addIssue({ code: "custom", path: ["limit"], message: t.add.kbLimitRange });
	});
type SettingsValues = z.infer<typeof settingsSchema>;

/**
 * Name, description and the knowledge-base limit override: "Follow the plan" (`max_corpora:
 * null`) or a number 1–10000 → `PUT /admin/tenants/{id}` (never `is_active`).
 */
export function SettingsTab({ tenant }: { tenant: AdminTenant }) {
	const ids = { name: useId(), description: useId(), limit: useId(), follow: useId(), custom: useId() };
	const plans = useQuery(plansQuery);
	const update = useUpdateTenant(tenant.id);
	const form = useAppForm<SettingsValues>({
		resolver: zodResolver(settingsSchema),
		defaultValues: {
			name: tenant.name,
			description: tenant.description ?? "",
			mode: tenant.max_corpora == null ? "plan" : "custom",
			limit: tenant.max_corpora == null ? "" : String(tenant.max_corpora),
		},
	});
	const errs = form.formState.errors;
	const mode = useWatch({ control: form.control, name: "mode" });
	const planLimit = plans.data?.items.find((p) => p.id === tenant.plan_id)?.max_knowledge_bases;

	const submit = useAppSubmit(
		form,
		async (v) => {
			const n = v.name.trim();
			const custom = v.mode === "custom" ? parseKbLimit(v.limit) : null;
			await update.mutateAsync({ name: n, description: v.description.trim() || null, max_corpora: custom ?? null });
			toast.success(t.detail.saved, { description: n });
		},
		{ fieldName: (path) => (path === "name" ? "name" : path === "max_corpora" ? "limit" : null) },
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate>
			<Panel title={t.detail.settingsTitle}>
				<div className="grid gap-4 p-4">
					{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
					<div className="grid gap-4 min-[861px]:grid-cols-2">
						<Field id={ids.name} label={t.detail.name} error={errs.name?.message}>
							<Input
								id={ids.name}
								autoComplete="off"
								aria-invalid={!!errs.name || undefined}
								aria-describedby={describedBy(ids.name, { error: !!errs.name })}
								{...form.register("name")}
							/>
						</Field>
						<Field id={ids.description} label={t.detail.description}>
							<Input id={ids.description} autoComplete="off" {...form.register("description")} />
						</Field>
					</div>
					<fieldset className="grid gap-2.5">
						<legend className="mb-1.5 text-[13px] font-semibold">{t.detail.kbLimit}</legend>
						<label htmlFor={ids.follow} className="flex items-center gap-2 text-[13.5px]">
							<input
								id={ids.follow}
								type="radio"
								value="plan"
								className="size-4 accent-[var(--primary)]"
								{...form.register("mode")}
							/>
							{fill(t.detail.followPlan, { limit: formatLimit(planLimit) })}
						</label>
						<label htmlFor={ids.custom} className="flex items-center gap-2 text-[13.5px]">
							<input
								id={ids.custom}
								type="radio"
								value="custom"
								className="size-4 accent-[var(--primary)]"
								{...form.register("mode")}
							/>
							{t.detail.custom}
						</label>
						{mode === "custom" ? (
							<div className="max-w-[260px] pl-6">
								<Field id={ids.limit} label={t.detail.customLabel} hint={t.add.kbLimitHint} error={errs.limit?.message}>
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
						) : null}
					</fieldset>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
						{t.detail.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
