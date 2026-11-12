import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { FormFoot, Panel } from "@/features/kbs/ui";
import type { BillingAccount, PricingFields } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date"; // TV3 notice
import { billingStrings as s, customerBillingStrings as t } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises"; // TV3 notice
import { planPricingQuery, useUpdateBillingAccount } from "../api";
import { FieldControl } from "../FieldControl";
import {
	displayValue,
	FIELDS,
	type FormValue,
	type FormValues,
	fieldLabel,
	GROUPS,
	type PricingKey,
	parseInput,
	toFormValues,
	toInput,
} from "../fields";

import { AgreedField, type Agreement, agreementBody, agreementValid, NOT_AGREED } from "./AgreedField"; // TV3 notice

type Drafts = Partial<Record<PricingKey, FormValue>>;

/** The drafts a stored override set starts from: only the keys the customer overrides. */
function initialDrafts(account: BillingAccount): Drafts {
	const drafts: Drafts = {};
	for (const d of FIELDS) {
		if (d.key in account.overrides) {
			drafts[d.key] = toInput(d, account.overrides[d.key] as PricingFields[PricingKey]);
		}
	}
	return drafts;
}

/**
 * The customer's terms: every pricing field with what they pay now, a "Custom" switch to give them
 * their own value, and the plan's value beside a custom one. Saving sends only what changed
 * (`overrides`; null removes an override so the plan applies again).
 */
export function TermsPanel({ account }: { account: BillingAccount }) {
	const baseId = useId();
	const plan = useQuery(planPricingQuery(account.plan.id));
	const update = useUpdateBillingAccount(account.tenant_id);
	const [drafts, setDrafts] = useState<Drafts>(() => initialDrafts(account));
	const [errors, setErrors] = useState<Partial<Record<PricingKey, string>>>({});
	const [failure, setFailure] = useState<string | null>(null);
	const [agreement, setAgreement] = useState<Agreement>(NOT_AGREED); // TV3 notice
	const [showReasonError, setShowReasonError] = useState(false); // TV3 notice
	const currency = account.currency;
	const effective = toFormValues(account.effective);
	const values = { ...effective, ...drafts } as FormValues;

	const toggle = (key: PricingKey, on: boolean) => {
		setErrors((e) => ({ ...e, [key]: undefined }));
		setDrafts((d) => {
			const next = { ...d };
			if (on) next[key] = effective[key];
			else delete next[key];
			return next;
		});
	};

	const save = async () => {
		setFailure(null);
		const body: Record<string, unknown> = {};
		const found: Partial<Record<PricingKey, string>> = {};
		for (const d of FIELDS) {
			const stored = d.key in account.overrides;
			const draft = drafts[d.key];
			if (draft === undefined) {
				if (stored) body[d.key] = null;
				continue;
			}
			const parsed = parseInput(d, draft, currency);
			if (!parsed.ok) {
				found[d.key] = parsed.error;
				continue;
			}
			const before = stored ? JSON.stringify(account.overrides[d.key]) : undefined;
			if (before !== JSON.stringify(parsed.value)) body[d.key] = parsed.value;
		}
		setErrors(found);
		if (Object.keys(found).length > 0) return;
		if (Object.keys(body).length === 0) {
			toast.info(t.terms.nothing);
			return;
		}
		// TV3 notice: a raise that the business did not ask for waits for the notice period
		setShowReasonError(true);
		if (!agreementValid(agreement)) return;
		try {
			const saved = await update.mutateAsync({ overrides: body, ...agreementBody(agreement) });
			if (saved.pending_change) {
				toast.success(noSurprisesStrings.pending.scheduledToast, {
					description: fill(noSurprisesStrings.pending.scheduledToastBody, {
						name: account.tenant_name,
						date: formatDate(saved.pending_change.effective_at),
					}),
				});
			} else {
				toast.success(t.terms.saved, { description: fill(t.terms.savedBody, { name: account.tenant_name }) });
			}
			setAgreement(NOT_AGREED);
			setShowReasonError(false);
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
			<Panel title={t.terms.title} sub={t.terms.lead}>
				<fieldset className="m-0 grid min-w-0 gap-5 border-0 p-4">
					<legend className="sr-only">{fill(t.terms.sectionLabel, { name: account.tenant_name })}</legend>
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					{GROUPS.map((g) => {
						const defs = FIELDS.filter((d) => d.group === g && (!d.showWhen || d.showWhen(values)));
						if (defs.length === 0) return null;
						return (
							<section key={g} className="grid gap-2">
								<h3 className="m-0 text-[13px] font-bold text-foreground-2">{s.groups[g]}</h3>
								<div className="grid divide-y divide-border rounded-lg border border-border">
									{defs.map((d) => {
										const custom = drafts[d.key] !== undefined;
										const id = `${baseId}-${d.key}`;
										const planValue = plan.data ? (plan.data as unknown as PricingFields)[d.key] : undefined;
										return (
											<div
												key={d.key}
												data-testid={`terms-row-${d.key}`}
												className="grid gap-2 p-3 min-[700px]:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] min-[700px]:items-center"
											>
												<div className="min-w-0">
													<div className="flex flex-wrap items-center gap-2 text-[13.5px] font-semibold">
														{fieldLabel(d.key)}
														{custom ? (
															<Badge variant="info" data-testid="custom-badge">
																{t.terms.badge}
															</Badge>
														) : null}
													</div>
													<small className="text-[12.5px] text-muted-foreground">
														{custom
															? plan.data
																? fill(t.terms.planValue, { value: displayValue(d, planValue, currency) })
																: null
															: t.terms.follow}
													</small>
												</div>
												<div className="min-w-0">
													{custom ? (
														<FieldControl
															def={d}
															id={id}
															showLabel={false}
															ariaLabel={fill(t.terms.valueAria, { field: fieldLabel(d.key) })}
															value={drafts[d.key] ?? ""}
															currency={currency}
															error={errors[d.key]}
															onChange={(v) => {
																setErrors((e) => ({ ...e, [d.key]: undefined }));
																setDrafts((x) => ({ ...x, [d.key]: v }));
															}}
														/>
													) : (
														<span className="text-[13.5px]">{displayValue(d, account.effective[d.key], currency)}</span>
													)}
												</div>
												<label
													className="flex items-center gap-2 text-[13px] text-foreground-2"
													htmlFor={`${id}-custom`}
												>
													<Switch
														id={`${id}-custom`}
														size="sm"
														checked={custom}
														aria-label={fill(t.terms.overrideAria, { field: fieldLabel(d.key) })}
														onCheckedChange={(on) => toggle(d.key, on)}
													/>
													{t.terms.customSwitch}
												</label>
											</div>
										);
									})}
								</div>
							</section>
						);
					})}
				</fieldset>
				{/* TV3 notice */}
				<div className="border-t border-border p-4">
					<AgreedField value={agreement} onChange={setAgreement} showError={showReasonError} />
				</div>
				{/* /TV3 notice */}
				<FormFoot>
					<SubmitButton variant="default" pending={update.isPending} pendingLabel={t.terms.saving}>
						{t.terms.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
