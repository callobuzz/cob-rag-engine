import { useId } from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Input } from "@/components/ui/input";
import type { Profile } from "@/features/pipelines/api";
import { fill } from "@/lib/strings/en";
import { customerLimitsStrings as t } from "@/lib/strings/pipelines";
import type { FairUsePolicy } from "./limits";
import { FREE, type OverrideErrors, type PolicyOverrides } from "./overrides";

/**
 * What happens for one business: each choice follows the plan unless the deployer sets one of its
 * own. The plan's value is shown in the "Follow the plan" option so the choice is informed.
 */
export function PolicyFields({
	value,
	onChange,
	plan,
	profiles,
	errors,
}: {
	value: PolicyOverrides;
	onChange: (next: PolicyOverrides) => void;
	plan: FairUsePolicy;
	profiles: Profile[] | undefined;
	errors: OverrideErrors;
}) {
	const p = t.policy;
	const ids = { b: useId(), e: useId(), s: useId(), h: useId(), a: useId() };
	const set = (patch: Partial<PolicyOverrides>) => onChange({ ...value, ...patch });
	const planProfile = plan.downgrade_profile_id
		? (profiles?.find((x) => x.id === plan.downgrade_profile_id)?.name ?? p.ownProfile)
		: p.ownProfile;
	return (
		<div className="grid gap-3 min-[700px]:grid-cols-2">
			<Field id={ids.b} label={p.billing}>
				<NativeSelect
					id={ids.b}
					value={value.on_billing_block}
					onChange={(e) => set({ on_billing_block: e.target.value })}
				>
					<option value="">
						{fill(p.follow, { value: p.billingNames[plan.on_billing_block] ?? plan.on_billing_block })}
					</option>
					{Object.entries(p.billingNames).map(([k, label]) => (
						<option key={k} value={k}>
							{label}
						</option>
					))}
				</NativeSelect>
			</Field>
			<Field id={ids.e} label={p.exceeded}>
				<NativeSelect
					id={ids.e}
					value={value.on_fair_use_exceeded}
					onChange={(e) => set({ on_fair_use_exceeded: e.target.value })}
				>
					<option value="">
						{fill(p.follow, { value: p.exceededNames[plan.on_fair_use_exceeded] ?? plan.on_fair_use_exceeded })}
					</option>
					{Object.entries(p.exceededNames).map(([k, label]) => (
						<option key={k} value={k}>
							{label}
						</option>
					))}
				</NativeSelect>
			</Field>
			<Field id={ids.s} label={p.slowTo}>
				<NativeSelect
					id={ids.s}
					value={value.downgrade_profile_id}
					onChange={(e) => set({ downgrade_profile_id: e.target.value })}
				>
					<option value="">{fill(p.follow, { value: planProfile })}</option>
					<option value={FREE}>{p.ownProfile}</option>
					{(profiles ?? []).map((x) => (
						<option key={x.id} value={x.id}>
							{x.name}
						</option>
					))}
				</NativeSelect>
			</Field>
			<div className="grid gap-3 min-[500px]:grid-cols-2 min-[700px]:grid-cols-1">
				<Field id={ids.h} label={p.hardLine} error={errors.hard_limit_pct}>
					<Input
						id={ids.h}
						inputMode="numeric"
						placeholder={fill(p.follow, { value: `${plan.hard_limit_pct}%` })}
						aria-invalid={errors.hard_limit_pct ? true : undefined}
						aria-describedby={describedBy(ids.h, { error: !!errors.hard_limit_pct })}
						value={value.hard_limit_pct}
						onChange={(e) => set({ hard_limit_pct: e.target.value })}
					/>
				</Field>
				<Field id={ids.a} label={p.alerts} error={errors.alert_thresholds}>
					<Input
						id={ids.a}
						placeholder={fill(p.follow, { value: (plan.alert_thresholds_pct ?? []).join(", ") })}
						aria-invalid={errors.alert_thresholds ? true : undefined}
						aria-describedby={describedBy(ids.a, { error: !!errors.alert_thresholds })}
						value={value.alert_thresholds}
						onChange={(e) => set({ alert_thresholds: e.target.value })}
					/>
				</Field>
			</div>
		</div>
	);
}
