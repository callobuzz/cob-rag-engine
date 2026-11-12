import { useId, useMemo, useState } from "react";
import { Controller, type Resolver, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
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
import { InclusiveHint } from "@/features/invoices/InclusiveHint"; // P2 invoices
import type { PlanPricing, PricingFields, PricingPreview } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors"; // TV3 notice
import { bpToPercent, type Currency, formatMinor } from "@/lib/format/money";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { billingStrings as s } from "@/lib/strings/billing";
import { en, fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises"; // TV3 notice
import { usePreviewPricing, useSavePricing } from "../api"; // TV3 notice
import { FieldControl } from "../FieldControl";
import {
	FIELDS,
	type FormValues,
	GROUPS,
	PRICING_DEFAULTS,
	type PricingKey,
	previewFor,
	toFormValues,
	toRequest,
} from "../fields";
import { istDay, longDay, ScheduleDialog } from "./ScheduleDialog"; // TV3 notice

type DialogValues = FormValues & { currency: Currency };

const resolver: Resolver<DialogValues> = (values) => {
	const { errors } = toRequest(values, values.currency);
	const keys = Object.keys(errors) as PricingKey[];
	if (keys.length === 0) return { values, errors: {} };
	return {
		values: {},
		errors: Object.fromEntries(keys.map((k) => [k, { type: "validate", message: errors[k] }])),
	};
};

/**
 * Edit one plan's pricing (BIL2): grouped fields with amounts in the plan's currency, a live
 * "What a customer pays" preview, and a save that creates the next version. Fields that do not
 * apply to the choices made above them (a price while charging is off) are hidden.
 */
export function PricingDialog({
	open,
	onOpenChange,
	planId,
	planName,
	current,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	planId: string;
	planName: string;
	/** The pricing in force (defaults when the plan has none). */
	current: PlanPricing;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[680px]">
				{open ? (
					<PricingForm planId={planId} planName={planName} current={current} onDone={() => onOpenChange(false)} />
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function PricingForm({
	planId,
	planName,
	current,
	onDone,
}: {
	planId: string;
	planName: string;
	current: PlanPricing;
	onDone: () => void;
}) {
	const baseId = useId();
	const save = useSavePricing();
	// TV3 notice: a rise waits for the businesses to be told; the preview decides
	const previewPricing = usePreviewPricing();
	const [held, setHeld] = useState<{ body: PricingFields; preview: PricingPreview } | null>(null);
	const [scheduleError, setScheduleError] = useState<string | null>(null);
	const confirmSchedule = async (effectiveAt: string) => {
		if (!held) return;
		setScheduleError(null);
		try {
			const saved = await save.mutateAsync({
				planId,
				body: { ...held.body, apply: "scheduled", effective_at: effectiveAt },
			});
			toast.success(noSurprisesStrings.schedule.scheduled, {
				description: fill(noSurprisesStrings.schedule.scheduledBody, {
					name: planName,
					date: longDay(istDay(saved.scheduled?.effective_at ?? effectiveAt)),
					count: saved.workspaces ?? held.preview.workspaces,
				}),
			});
			setHeld(null);
			onDone();
		} catch (e) {
			setScheduleError(messageFor(toApiError(e)).title);
		}
	};
	// /TV3 notice
	const initial = useMemo(
		() => ({ ...toFormValues({ ...PRICING_DEFAULTS, ...current }), currency: current.currency }),
		[current],
	);
	const form = useAppForm<DialogValues>({ resolver, defaultValues: initial });
	const values = useWatch({ control: form.control }) as DialogValues;
	const currency = values.currency ?? current.currency;
	const errs = form.formState.errors as Partial<Record<PricingKey, { message?: string }>>;
	const preview = useMemo(() => toRequest(values, currency).body, [values, currency]);

	const submit = useAppSubmit(form, async (v) => {
		const { body } = toRequest(v, v.currency);
		const before = toRequest(initial, initial.currency).body;
		const changed = JSON.stringify(body) !== JSON.stringify(before);
		if (!changed && current.version != null) {
			toast.info(fill(s.dialog.savedNoChange, { version: String(current.version) }));
			onDone();
			return;
		}
		// TV3 notice: ask what this means first; a change that costs a business more is announced
		const asked = await previewPricing.mutateAsync({ planId, body: { ...body, apply: "now" } });
		if (asked.workspaces > 0 && (asked.impact === "higher" || asked.impact === "mixed")) {
			setHeld({ body, preview: asked });
			return;
		}
		// /TV3 notice
		const saved = await save.mutateAsync({ planId, body: { ...body, apply: "now" } });
		toast.success(s.dialog.saved, {
			description: fill(s.dialog.savedBody, { name: planName, version: String(saved.version ?? 1) }),
		});
		onDone();
	});

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-5">
			<DialogHeader>
				<DialogTitle>{fill(s.dialog.title, { name: planName })}</DialogTitle>
				<DialogDescription>{fill(s.dialog.lead, { currency })}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
			<Field id={`${baseId}-currency`} label={s.dialog.currency} hint={s.dialog.currencyHint}>
				<Controller
					control={form.control}
					name="currency"
					render={({ field }) => (
						<NativeSelect
							id={`${baseId}-currency`}
							value={field.value}
							aria-describedby={`${baseId}-currency-hint`}
							onChange={(e) => field.onChange(e.target.value)}
						>
							<option value="INR">{s.dialog.currencies.INR}</option>
							<option value="USD">{s.dialog.currencies.USD}</option>
						</NativeSelect>
					)}
				/>
			</Field>
			{GROUPS.map((g) => {
				const defs = FIELDS.filter((d) => d.group === g && (!d.showWhen || d.showWhen(values)));
				return (
					<fieldset key={g} className="grid gap-3 rounded-lg border border-border p-4">
						<legend className="px-1 text-[13.5px] font-bold">{s.groups[g]}</legend>
						<p className="m-0 text-[12.5px] text-muted-foreground">{s.groupLeads[g]}</p>
						<div className="grid gap-4 min-[600px]:grid-cols-2">
							{defs.map((d) => (
								<div key={d.key} className={d.kind === "bool" ? "min-[600px]:col-span-2" : undefined}>
									<Controller
										control={form.control}
										name={d.key}
										render={({ field }) => (
											<FieldControl
												def={d}
												id={`${baseId}-${d.key}`}
												value={field.value as string | boolean}
												currency={currency}
												error={errs[d.key]?.message}
												onChange={field.onChange}
												onBlur={field.onBlur}
											/>
										)}
									/>
								</div>
							))}
						</div>
					</fieldset>
				);
			})}
			<section
				aria-label={s.dialog.previewTitle}
				data-testid="pricing-preview"
				className="grid gap-2 rounded-lg border border-border bg-secondary p-4"
			>
				<h3 className="m-0 text-[13.5px] font-bold">{s.dialog.previewTitle}</h3>
				{[100, 1000].map((n) => {
					const p = previewFor(preview, n);
					const usage =
						p.usage.kind === "amount"
							? formatMinor(p.usage.minor, currency)
							: p.usage.kind === "aiCost"
								? fill(s.dialog.previewAiCost, { pct: bpToPercent(p.usage.bp) })
								: null;
					return (
						<div key={n} className="flex flex-wrap items-baseline justify-between gap-2 text-[13px]">
							<span>{fill(s.dialog.previewLine, { count: n.toLocaleString("en-IN") })}</span>
							<span className="font-mono tabular-nums">
								{p.total != null
									? formatMinor(p.total, currency)
									: [p.fee > 0 ? formatMinor(p.fee, currency) : null, usage].filter(Boolean).join(" + ")}
							</span>
						</div>
					);
				})}
				<InclusiveHint feeMinor={preview.subscription_fee_minor} currency={currency} /> {/* P2 invoices */}
				<small className="text-[12.5px] text-muted-foreground">{s.dialog.previewNote}</small>
			</section>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={s.dialog.saving}
				>
					{s.dialog.save}
				</SubmitButton>
			</DialogFooter>
			{/* TV3 notice */}
			{held ? (
				<ScheduleDialog
					open
					workspaces={held.preview.workspaces}
					earliest={held.preview.earliest_effective_at}
					replaces={current.scheduled != null}
					pending={save.isPending}
					error={scheduleError}
					onConfirm={(at) => void confirmSchedule(at)}
					onBack={() => setHeld(null)}
				/>
			) : null}
			{/* /TV3 notice */}
		</form>
	);
}
