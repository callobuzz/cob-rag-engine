import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Input } from "@/components/ui/input";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { plansQuery } from "@/features/plans/api";
import type { BillingAccount } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatNumber } from "@/lib/format/number";
import { customerBillingStrings as t } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { useUpdateBillingAccount } from "../api";

const LIMITS = [
	"max_knowledge_bases",
	"monthly_questions",
	"max_documents",
	"max_widget_keys",
	"max_upload_mb",
	"max_document_tokens",
] as const;
type LimitKey = (typeof LIMITS)[number];
type Mode = "follow" | "custom" | "unlimited";
interface Draft {
	mode: Mode;
	value: string;
}

function initial(account: BillingAccount): Record<LimitKey, Draft> {
	return Object.fromEntries(
		LIMITS.map((k): [LimitKey, Draft] => {
			if (!(k in account.limit_overrides)) return [k, { mode: "follow", value: "" }];
			const v = account.limit_overrides[k];
			return v == null ? [k, { mode: "unlimited", value: "" }] : [k, { mode: "custom", value: String(v) }];
		}),
	) as Record<LimitKey, Draft>;
}

/** A whole number of 1 or more (grouping commas allowed), or null. */
function parseCount(text: string): number | null {
	const cleaned = text.replace(/[,\s_]/g, "");
	if (!/^\d{1,9}$/.test(cleaned)) return null;
	const n = Number(cleaned);
	return n >= 1 ? n : null;
}

/**
 * Per-customer limits: follow the plan, set a number, or remove the limit. Saving replaces the
 * stored set (a limit left on "Follow the plan" is not stored).
 */
export function LimitsPanel({ account }: { account: BillingAccount }) {
	const baseId = useId();
	const plans = useQuery(plansQuery);
	const update = useUpdateBillingAccount(account.tenant_id);
	const [drafts, setDrafts] = useState(() => initial(account));
	const [errors, setErrors] = useState<Partial<Record<LimitKey, string>>>({});
	const [failure, setFailure] = useState<string | null>(null);
	const plan = plans.data?.items.find((p) => p.id === account.plan.id);

	const set = (k: LimitKey, patch: Partial<Draft>) => {
		setErrors((e) => ({ ...e, [k]: undefined }));
		setDrafts((d) => ({ ...d, [k]: { ...d[k], ...patch } }));
	};

	const planText = (k: LimitKey): string | null => {
		if (!plan) return null;
		const v = plan[k];
		return v == null ? t.limits.unlimited : formatNumber(v);
	};

	const save = async () => {
		setFailure(null);
		const body: Record<string, number | null> = {};
		const found: Partial<Record<LimitKey, string>> = {};
		for (const k of LIMITS) {
			const d = drafts[k];
			if (d.mode === "unlimited") body[k] = null;
			else if (d.mode === "custom") {
				const n = parseCount(d.value);
				if (n == null) found[k] = t.limits.invalid;
				else body[k] = n;
			}
		}
		setErrors(found);
		if (Object.keys(found).length > 0) return;
		try {
			await update.mutateAsync({ limit_overrides: body });
			toast.success(t.limits.saved, { description: fill(t.limits.savedBody, { name: account.tenant_name }) });
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};

	return (
		<form
			onSubmit={(e) => {
				e.preventDefault();
				void save();
			}}
			noValidate
		>
			<Panel title={t.limits.title} sub={t.limits.lead}>
				<div className="grid gap-3 p-4">
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					<div className="grid divide-y divide-border rounded-lg border border-border">
						{LIMITS.map((k) => {
							const d = drafts[k];
							const id = `${baseId}-${k}`;
							const label = t.limits.fields[k];
							return (
								<div
									key={k}
									data-testid={`limit-row-${k}`}
									className="grid gap-2 p-3 min-[700px]:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] min-[700px]:items-center"
								>
									<div className="min-w-0 text-[13.5px] font-semibold">
										{label}
										{planText(k) ? (
											<small className="block text-[12.5px] font-normal text-muted-foreground">
												{fill(t.terms.planValue, { value: planText(k) ?? "" })}
											</small>
										) : null}
									</div>
									<div className="grid gap-1.5 min-[480px]:grid-cols-2">
										<NativeSelect
											id={id}
											value={d.mode}
											aria-label={fill(t.limits.modeAria, { field: label })}
											onChange={(e) => set(k, { mode: e.target.value as Mode })}
										>
											<option value="follow">{t.limits.follow}</option>
											<option value="custom">{t.limits.custom}</option>
											<option value="unlimited">{t.limits.unlimited}</option>
										</NativeSelect>
										{d.mode === "custom" ? (
											<div className="grid gap-1">
												<Input
													inputMode="numeric"
													autoComplete="off"
													className="font-mono"
													value={d.value}
													aria-label={fill(t.limits.numberAria, { field: label })}
													aria-invalid={!!errors[k] || undefined}
													onChange={(e) => set(k, { value: e.target.value })}
												/>
												{errors[k] ? (
													<small className="text-[12.5px] font-medium text-destructive">{errors[k]}</small>
												) : null}
											</div>
										) : null}
									</div>
								</div>
							);
						})}
					</div>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={update.isPending} pendingLabel={t.terms.saving}>
						{t.limits.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
