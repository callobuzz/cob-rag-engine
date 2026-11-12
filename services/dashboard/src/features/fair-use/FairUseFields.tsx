import { useId } from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Input } from "@/components/ui/input";
import type { Profile } from "@/features/pipelines/api";
import { formatMoney, useCurrency } from "@/lib/format/currency";
import { fill } from "@/lib/strings/en";
import { fairUseStrings, planFairUseStrings as t } from "@/lib/strings/pipelines";
import {
	DEFAULT_POLICY,
	type DraftErrors,
	type FairUseDraft,
	GROUP_ORDER,
	type LimitDef,
	PLAN_LIMIT_DEFS,
	type PolicyDraft,
	usdToMicro,
} from "./limits";

/** The plan's "Answer speed" select: a pipeline profile, or the engine's default. */
export function AnswerSpeedField({
	draft,
	onChange,
	profiles,
	readOnly,
}: {
	draft: FairUseDraft;
	onChange: (next: FairUseDraft) => void;
	profiles: Profile[] | undefined;
	readOnly?: boolean;
}) {
	const id = useId();
	const fallback = profiles?.find((p) => p.is_default);
	return (
		<fieldset className="m-0 grid gap-3 rounded-md border border-border p-3.5">
			<legend className="px-1.5 text-[13.5px] font-bold">{t.speed.title}</legend>
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.speed.lead}</p>
			<Field id={id} label={t.speed.field} hint={t.speed.hint}>
				<NativeSelect
					id={id}
					aria-describedby={`${id}-hint`}
					disabled={readOnly || !profiles}
					value={draft.profileId}
					onChange={(e) => onChange({ ...draft, profileId: e.target.value })}
				>
					<option value="">{fill(t.speed.default, { label: fallback?.label ?? "Standard" })}</option>
					{(profiles ?? [])
						.filter((p) => !p.is_default)
						.map((p) => (
							<option key={p.id} value={p.id}>
								{fill(t.speed.option, { name: p.name, label: p.label })}
							</option>
						))}
					{fallback && draft.profileId === fallback.id ? (
						<option value={fallback.id}>{fill(t.speed.option, { name: fallback.name, label: fallback.label })}</option>
					) : null}
				</NativeSelect>
			</Field>
		</fieldset>
	);
}

function LimitField({
	def,
	value,
	error,
	onChange,
	readOnly,
}: {
	def: LimitDef;
	value: string;
	error?: string;
	onChange: (v: string) => void;
	readOnly?: boolean;
}) {
	const id = useId();
	useCurrency();
	const micro = def.unit === "usd" ? usdToMicro(value) : null;
	const hint =
		def.unit === "usd"
			? micro
				? fill(t.spendHint, { inr: formatMoney(micro / 1_000_000, "INR") })
				: t.spendHintEmpty
			: t.noLimitHint;
	return (
		<Field id={id} label={fairUseStrings.metrics[def.metric] ?? def.metric} hint={hint} error={error}>
			<Input
				id={id}
				inputMode={def.unit === "usd" ? "decimal" : "numeric"}
				autoComplete="off"
				className="font-mono"
				placeholder={t.noLimit}
				readOnly={readOnly}
				aria-invalid={error ? true : undefined}
				aria-describedby={describedBy(id, { hint: true, error: !!error })}
				value={value}
				onChange={(e) => onChange(e.target.value)}
			/>
		</Field>
	);
}

function Choice({
	name,
	value,
	checked,
	label,
	body,
	onSelect,
	disabled,
}: {
	name: string;
	value: string;
	checked: boolean;
	label: string;
	body: string;
	onSelect: () => void;
	disabled?: boolean;
}) {
	const id = useId();
	return (
		<div className="flex items-start gap-2.5">
			<input
				id={id}
				type="radio"
				name={name}
				value={value}
				checked={checked}
				disabled={disabled}
				aria-describedby={`${id}-body`}
				className="mt-1 size-4 accent-[var(--primary)]"
				onChange={onSelect}
			/>
			<div className="grid gap-0.5">
				<label htmlFor={id} className="text-[13.5px] font-semibold">
					{label}
				</label>
				<small id={`${id}-body`} className="text-[12.5px] text-muted-foreground">
					{body}
				</small>
			</div>
		</div>
	);
}

/** The plan's "Fair use" section: the limits, what happens past them, and the advanced levels. */
export function FairUseFields({
	draft,
	onChange,
	errors,
	profiles,
	readOnly,
}: {
	draft: FairUseDraft;
	onChange: (next: FairUseDraft) => void;
	errors: DraftErrors;
	profiles: Profile[] | undefined;
	readOnly?: boolean;
}) {
	const uid = useId();
	const setPolicy = (patch: Partial<PolicyDraft>) => onChange({ ...draft, policy: { ...draft.policy, ...patch } });
	const planProfileId = draft.profileId || profiles?.find((p) => p.is_default)?.id || "";
	const targets = (profiles ?? []).filter((p) => p.id !== planProfileId);
	const hardId = useId();
	const alertId = useId();
	const slowId = useId();
	const h = t.happens;
	return (
		<fieldset className="m-0 grid gap-4 rounded-md border border-border p-3.5">
			<legend className="px-1.5 text-[13.5px] font-bold">{t.title}</legend>
			<p className="m-0 text-[12.5px] text-muted-foreground">{readOnly ? t.internalLocked : t.lead}</p>
			{GROUP_ORDER.map((g) => {
				const defs = PLAN_LIMIT_DEFS.filter((d) => d.group === g);
				return (
					<fieldset key={g} className="m-0 grid gap-2 border-0 p-0">
						<legend className="mb-1 p-0 text-[12.5px] font-bold tracking-wide text-muted-foreground uppercase">
							{fairUseStrings.groups[g]}
						</legend>
						<div className="grid gap-3 min-[600px]:grid-cols-2">
							{defs.map((d) => (
								<LimitField
									key={d.field}
									def={d}
									value={draft.limits[d.field] ?? ""}
									error={errors[d.field]}
									readOnly={readOnly}
									onChange={(v) => onChange({ ...draft, limits: { ...draft.limits, [d.field]: v } })}
								/>
							))}
						</div>
						{g === "website" ? <small className="text-[12.5px] text-muted-foreground">{t.totalPagesNote}</small> : null}
					</fieldset>
				);
			})}

			<div className="grid gap-3 border-t border-border pt-3.5">
				<h4 className="m-0 text-[13.5px] font-bold">{h.title}</h4>
				<p className="m-0 text-[12.5px] text-muted-foreground">{h.lead}</p>
				<div role="radiogroup" aria-label={h.billing} className="grid gap-2.5">
					<b className="text-[13px]">{h.billing}</b>
					{(Object.keys(h.billingChoices) as (keyof typeof h.billingChoices)[]).map((k) => (
						<Choice
							key={k}
							name={`${uid}-billing`}
							value={k}
							checked={draft.policy.on_billing_block === k}
							label={h.billingChoices[k].label}
							body={h.billingChoices[k].body}
							disabled={readOnly}
							onSelect={() => setPolicy({ on_billing_block: k })}
						/>
					))}
				</div>
				<div role="radiogroup" aria-label={h.exceeded} className="grid gap-2.5">
					<b className="text-[13px]">{h.exceeded}</b>
					{(Object.keys(h.exceededChoices) as (keyof typeof h.exceededChoices)[]).map((k) => (
						<Choice
							key={k}
							name={`${uid}-exceeded`}
							value={k}
							checked={draft.policy.on_fair_use_exceeded === k}
							label={h.exceededChoices[k].label}
							body={h.exceededChoices[k].body}
							disabled={readOnly}
							onSelect={() => setPolicy({ on_fair_use_exceeded: k })}
						/>
					))}
				</div>
				<Field id={slowId} label={h.slowTo} hint={h.slowToHint}>
					<NativeSelect
						id={slowId}
						aria-describedby={`${slowId}-hint`}
						disabled={readOnly}
						value={draft.policy.downgrade_profile_id}
						onChange={(e) => setPolicy({ downgrade_profile_id: e.target.value })}
					>
						<option value="">{h.slowToDefault}</option>
						{targets.map((p) => (
							<option key={p.id} value={p.id}>
								{p.name}
							</option>
						))}
					</NativeSelect>
				</Field>
				<details className="rounded-md border border-border p-3">
					<summary className="cursor-pointer text-[13px] font-semibold">{h.advanced}</summary>
					<div className="mt-3 grid gap-3 min-[600px]:grid-cols-2">
						<Field id={hardId} label={h.hardLine} hint={h.hardLineHint} error={errors.hard_limit_pct}>
							<Input
								id={hardId}
								inputMode="numeric"
								readOnly={readOnly}
								aria-invalid={errors.hard_limit_pct ? true : undefined}
								aria-describedby={describedBy(hardId, { hint: true, error: !!errors.hard_limit_pct })}
								placeholder={String(DEFAULT_POLICY.hard_limit_pct)}
								value={draft.policy.hard_limit_pct}
								onChange={(e) => setPolicy({ hard_limit_pct: e.target.value })}
							/>
						</Field>
						<Field id={alertId} label={h.alerts} hint={h.alertsHint} error={errors.alert_thresholds}>
							<Input
								id={alertId}
								readOnly={readOnly}
								aria-invalid={errors.alert_thresholds ? true : undefined}
								aria-describedby={describedBy(alertId, { hint: true, error: !!errors.alert_thresholds })}
								placeholder="80, 100"
								value={draft.policy.alert_thresholds}
								onChange={(e) => setPolicy({ alert_thresholds: e.target.value })}
							/>
						</Field>
					</div>
				</details>
			</div>
		</fieldset>
	);
}
