import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { type FairUseAdminView, fairUseTenantQuery, useSetFairUse } from "@/features/fair-use/api";
import { AdminMetricMeter } from "@/features/fair-use/FairUseMeters";
import { StateBadge } from "@/features/fair-use/labels";
import { GROUP_ORDER, LIMIT_DEFS, type LimitDef } from "@/features/fair-use/limits";
import {
	buildOverrides,
	initialPolicy,
	initialRows,
	type LimitRow,
	type LimitRows,
	limitPhrase,
	type Mode,
	type OverrideErrors,
	type PolicyOverrides,
	planLimit,
	planPolicy,
} from "@/features/fair-use/overrides";
import { PolicyFields } from "@/features/fair-use/PolicyFields";
import { FormFoot, Panel } from "@/features/kbs/ui";
import {
	type Profile,
	pipelinesQuery,
	type TenantPipeline,
	tenantPipelineQuery,
	useSetTenantPipeline,
} from "@/features/pipelines/api";
import { plansQuery } from "@/features/plans/api";
import type { AdminTenant, Plan } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { fairUseStrings, planFairUseStrings, customerLimitsStrings as t } from "@/lib/strings/pipelines";

/**
 * Answer speed & limits (PIP4): which pipeline profile this customer answers with, how much of each
 * limit it has used, what the engine decided today, and every limit and policy choice with "follow
 * the plan", an own value, or no limit. The operator's own workspace is never limited.
 */
export function LimitsTab({ tenant }: { tenant: AdminTenant }) {
	const view = useQuery(fairUseTenantQuery(tenant.id));
	const pipeline = useQuery(tenantPipelineQuery(tenant.id));
	const profiles = useQuery(pipelinesQuery);
	const plans = useQuery(plansQuery);

	if (tenant.is_operator || view.data?.exempt) {
		return (
			<Panel title={t.exempt} sub={t.exemptBody}>
				<p data-testid="customer-limits-exempt" className="m-0 px-4 py-4 text-[13.5px]">
					{t.exemptBody}
				</p>
			</Panel>
		);
	}
	if (view.isPending || pipeline.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={3} />
				<CardSkeleton lines={6} />
			</div>
		);
	}
	if (view.isError) return <ErrorState error={view.error} title={t.loadError} onRetry={() => void view.refetch()} />;
	if (pipeline.isError)
		return <ErrorState error={pipeline.error} title={t.loadError} onRetry={() => void pipeline.refetch()} />;
	const plan = plans.data?.items.find((p) => p.name === view.data.plan);
	return (
		<div className="grid items-start gap-4" data-testid="customer-limits">
			<SpeedPanel
				key={`${pipeline.data.override_profile_id ?? ""}:${pipeline.data.effective.id}`}
				tenantId={tenant.id}
				pipeline={pipeline.data}
				profiles={profiles.data?.items}
			/>
			<TodayPanel view={view.data} />
			<UsagePanel view={view.data} />
			<LimitsPanel
				key={JSON.stringify([view.data.limits, view.data.policy, view.data.overridden])}
				tenantId={tenant.id}
				view={view.data}
				plan={plan}
				profiles={profiles.data?.items}
			/>
		</div>
	);
}

function SpeedPanel({
	tenantId,
	pipeline,
	profiles,
}: {
	tenantId: string;
	pipeline: TenantPipeline;
	profiles: Profile[] | undefined;
}) {
	const id = useId();
	const set = useSetTenantPipeline(tenantId);
	const [value, setValue] = useState(pipeline.override_profile_id ?? "");
	const [error, setError] = useState<string | null>(null);
	const planProfile = profiles?.find((p) => p.id === pipeline.plan_profile_id);
	const dirty = value !== (pipeline.override_profile_id ?? "");
	const save = async (ev: React.FormEvent) => {
		ev.preventDefault();
		setError(null);
		try {
			await set.mutateAsync(value === "" ? null : value);
			toast.success(t.speed.saved);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<Panel title={t.speed.title} sub={t.speed.lead}>
			<form onSubmit={(e) => void save(e)} noValidate>
				<div className="grid gap-3 p-4">
					{error ? <FormAlert>{error}</FormAlert> : null}
					<Field id={id} label={t.speed.field}>
						<NativeSelect id={id} value={value} onChange={(e) => setValue(e.target.value)}>
							<option value="">
								{planProfile ? fill(t.speed.follow, { name: planProfile.name }) : t.speed.followUnknown}
							</option>
							{(profiles ?? []).map((p) => (
								<option key={p.id} value={p.id}>
									{p.name}
								</option>
							))}
						</NativeSelect>
					</Field>
					<p className="m-0 text-[12.5px] text-muted-foreground">
						{fill(t.speed.effective, { name: pipeline.effective.name, label: pipeline.effective.label })}
					</p>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={set.isPending} locked={!dirty}>
						{t.speed.save}
					</SubmitButton>
				</FormFoot>
			</form>
		</Panel>
	);
}

function TodayPanel({ view }: { view: FairUseAdminView }) {
	const d = view.decisions_today;
	const answered = view.usage.find((u) => u.metric === "questions_per_day")?.used;
	const parts = [
		answered != null ? fill(t.today.answered, { n: answered }) : null,
		d.downgraded > 0 ? fill(t.today.slower, { n: d.downgraded }) : null,
		d.paused > 0 ? fill(t.today.paused, { n: d.paused }) : null,
		d.busy > 0 ? fill(t.today.waited, { n: d.busy }) : null,
	].filter((x): x is string => x !== null);
	return (
		<Panel title={t.today.title} aside={<StateBadge state={view.state} />}>
			<div className="grid gap-1 px-4 py-3.5 text-[13.5px]">
				<p data-testid="fair-use-today" className="m-0 font-medium">
					{parts.length ? fill(t.today.line, { parts: parts.join(", ") }) : t.today.nothing}
				</p>
				{view.state !== "ok" && view.state_since ? (
					<small className="text-[12.5px] text-muted-foreground">
						{fill(t.today.since, { when: formatDateTime(view.state_since) })}
					</small>
				) : null}
			</div>
		</Panel>
	);
}

function UsagePanel({ view }: { view: FairUseAdminView }) {
	return (
		<Panel title={t.usage.title} sub={t.usage.lead}>
			<div className="grid gap-4 p-4">
				{view.usage.length === 0 ? (
					<p className="m-0 text-[13px] text-muted-foreground">{t.usage.none}</p>
				) : (
					view.usage.map((u) => <AdminMetricMeter key={u.metric} row={u} />)
				)}
			</div>
		</Panel>
	);
}

function LimitRowField({
	def,
	row,
	planValue,
	error,
	own,
	onChange,
}: {
	def: LimitDef;
	row: LimitRow;
	planValue: number | null;
	error?: string;
	own: boolean;
	onChange: (r: LimitRow) => void;
}) {
	const id = useId();
	const label = fairUseStrings.metrics[def.metric] ?? def.metric;
	const l = t.limits;
	return (
		<Field id={id} label={label} labelExtra={own ? <Badge variant="info">{l.changed}</Badge> : undefined} error={error}>
			<div className="flex flex-wrap items-center gap-2">
				<NativeSelect
					id={id}
					className="min-w-[200px] flex-1"
					value={row.mode}
					aria-describedby={describedBy(id, { error: !!error })}
					onChange={(e) => {
						// Starting an own limit starts from an empty field, not from the plan's number.
						const mode = e.target.value as Mode;
						onChange({ mode, text: mode === "custom" && row.mode !== "custom" ? "" : row.text });
					}}
				>
					<option value="follow">
						{planValue == null ? l.followNoLimit : fill(l.follow, { value: limitPhrase(def, planValue) })}
					</option>
					<option value="custom">{l.custom}</option>
					<option value="none">{l.none}</option>
				</NativeSelect>
				{row.mode === "custom" ? (
					<Input
						aria-label={fill(l.customLabel, { label })}
						inputMode={def.unit === "usd" ? "decimal" : "numeric"}
						className="w-[140px] font-mono"
						placeholder={def.unit === "usd" ? "$" : "0"}
						aria-invalid={error ? true : undefined}
						value={row.text}
						onChange={(e) => onChange({ ...row, text: e.target.value })}
					/>
				) : null}
			</div>
		</Field>
	);
}

function LimitsPanel({
	tenantId,
	view,
	plan,
	profiles,
}: {
	tenantId: string;
	view: FairUseAdminView;
	plan: Plan | undefined;
	profiles: Profile[] | undefined;
}) {
	const save = useSetFairUse(tenantId);
	const before = { rows: initialRows(view), policy: initialPolicy(view) };
	const [rows, setRows] = useState<LimitRows>(before.rows);
	const [policy, setPolicy] = useState<PolicyOverrides>(before.policy);
	const [errors, setErrors] = useState<OverrideErrors>({});
	const [alert, setAlert] = useState<string | null>(null);
	const own = new Set(view.overridden);

	const submit = async (ev: React.FormEvent) => {
		ev.preventDefault();
		setAlert(null);
		const built = buildOverrides(
			before,
			{ rows, policy },
			{
				limit: (def) => (def.unit === "usd" ? planFairUseStrings.spendInvalid : planFairUseStrings.limitInvalid),
				hardLine: planFairUseStrings.happens.hardLineInvalid,
				alerts: planFairUseStrings.happens.alertsInvalid,
			},
		);
		setErrors(built.errors);
		if (Object.keys(built.errors).length > 0 || built.empty) return;
		try {
			await save.mutateAsync(built.body);
			toast.success(t.limits.saved);
		} catch (e) {
			setAlert(messageFor(toApiError(e)).title);
		}
	};

	return (
		<Panel title={t.limits.title} sub={t.limits.lead}>
			<form onSubmit={(e) => void submit(e)} noValidate>
				<div className="grid gap-5 p-4">
					{alert ? <FormAlert>{alert}</FormAlert> : null}
					{GROUP_ORDER.map((g) => (
						<fieldset key={g} className="m-0 grid gap-3 border-0 p-0">
							<legend className="mb-1 p-0 text-[12.5px] font-bold tracking-wide text-muted-foreground uppercase">
								{fairUseStrings.groups[g]}
							</legend>
							<div className="grid gap-3 min-[700px]:grid-cols-2">
								{LIMIT_DEFS.filter((d) => d.group === g).map((def) => (
									<LimitRowField
										key={def.field}
										def={def}
										row={rows[def.field]}
										planValue={planLimit(plan, def.field)}
										error={errors[def.field]}
										own={own.has(def.field)}
										onChange={(r) => setRows((cur) => ({ ...cur, [def.field]: r }))}
									/>
								))}
							</div>
						</fieldset>
					))}
					<fieldset className="m-0 grid gap-3 border-0 p-0">
						<legend className="mb-1 p-0 text-[12.5px] font-bold tracking-wide text-muted-foreground uppercase">
							{t.policy.title}
						</legend>
						<p className="m-0 text-[12.5px] text-muted-foreground">{t.policy.lead}</p>
						<PolicyFields
							value={policy}
							onChange={setPolicy}
							plan={planPolicy(plan)}
							profiles={profiles}
							errors={errors}
						/>
					</fieldset>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={save.isPending}>
						{t.limits.save}
					</SubmitButton>
				</FormFoot>
			</form>
		</Panel>
	);
}
