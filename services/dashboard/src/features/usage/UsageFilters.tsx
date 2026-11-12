import { useId } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Segmented } from "@/components/patterns/Segmented";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { AdminTenant } from "@/lib/api/accounts-types";
import type { Corpus } from "@/lib/api/queries";
import { type DayRange, type RangeProblem, USAGE_PRESETS, type UsagePreset } from "@/lib/format/range";
import { tenantLabel } from "@/lib/format/tenant";
import { usageStrings as t } from "@/lib/strings/overview";
import type { UsageGroup } from "./api";

export interface UsageFilterValue {
	preset: UsagePreset;
	range: DayRange;
	kb?: string;
	tenant?: string;
	group: UsageGroup;
	/** Leave the operator's own workspace out (`exclude_operator`). */
	excludeOwn?: boolean;
}

/**
 * One row of filters above the chart and table (dataviz: filters scope everything below them):
 * date range (presets, then a custom range capped at 366 days), knowledge base, customer (admin)
 * and grouping. Every value lives in the URL.
 */
export function UsageFilters({
	value,
	admin,
	hasOwn = false,
	corpora,
	tenants,
	problem,
	onChange,
}: {
	value: UsageFilterValue;
	admin: boolean;
	/** The operator's own workspace exists (so the "leave it out" switch means something). */
	hasOwn?: boolean;
	corpora: Corpus[];
	tenants: AdminTenant[];
	problem: RangeProblem;
	onChange: (
		next: Partial<{
			range: UsagePreset;
			from: string;
			to: string;
			kb: string;
			tenant: string;
			group: UsageGroup;
			own: number;
		}>,
	) => void;
}) {
	const id = useId();
	const kbs = value.tenant ? corpora.filter((c) => c.tenant_id === value.tenant) : corpora;
	const groups: UsageGroup[] = admin
		? ["day", "corpus", "source", "tenant", "model"]
		: ["day", "corpus", "source", "model"];
	const byCustomer = new Map<string, Corpus[]>();
	for (const c of kbs) byCustomer.set(c.tenant_name, [...(byCustomer.get(c.tenant_name) ?? []), c]);
	return (
		<section aria-label={t.filters} className="grid gap-2">
			<div className="flex flex-wrap items-end gap-3">
				<div className="grid gap-1.5">
					<Label htmlFor={`${id}-range`}>{t.range}</Label>
					<NativeSelect
						id={`${id}-range`}
						className="w-[170px]"
						value={value.preset}
						onChange={(e) => {
							const preset = e.target.value as UsagePreset;
							onChange(
								preset === "custom"
									? { range: preset, from: value.range.start, to: value.range.end }
									: { range: preset, from: undefined, to: undefined },
							);
						}}
					>
						{USAGE_PRESETS.map((p) => (
							<option key={p} value={p}>
								{t.presets[p]}
							</option>
						))}
					</NativeSelect>
				</div>
				{value.preset === "custom" ? (
					<>
						<div className="grid gap-1.5">
							<Label htmlFor={`${id}-from`}>{t.from}</Label>
							<Input
								id={`${id}-from`}
								type="date"
								className="w-[160px]"
								value={value.range.start}
								aria-invalid={problem ? true : undefined}
								aria-describedby={problem ? `${id}-problem` : undefined}
								onChange={(e) => e.target.value && onChange({ from: e.target.value })}
							/>
						</div>
						<div className="grid gap-1.5">
							<Label htmlFor={`${id}-to`}>{t.to}</Label>
							<Input
								id={`${id}-to`}
								type="date"
								className="w-[160px]"
								value={value.range.end}
								aria-invalid={problem ? true : undefined}
								aria-describedby={problem ? `${id}-problem` : undefined}
								onChange={(e) => e.target.value && onChange({ to: e.target.value })}
							/>
						</div>
					</>
				) : null}
				{admin ? (
					<div className="grid gap-1.5">
						<Label htmlFor={`${id}-tenant`}>{t.customer}</Label>
						<NativeSelect
							id={`${id}-tenant`}
							className="w-[190px]"
							value={value.tenant ?? ""}
							onChange={(e) => onChange({ tenant: e.target.value || undefined, kb: undefined })}
						>
							<option value="">{t.allCustomers}</option>
							{tenants.map((x) => (
								<option key={x.id} value={x.id}>
									{tenantLabel(x)}
								</option>
							))}
						</NativeSelect>
					</div>
				) : null}
				<div className="grid gap-1.5">
					<Label htmlFor={`${id}-kb`}>{t.kb}</Label>
					<NativeSelect
						id={`${id}-kb`}
						className="w-[210px]"
						value={value.kb ?? ""}
						onChange={(e) => onChange({ kb: e.target.value || undefined })}
					>
						<option value="">{t.allKbs}</option>
						{admin && !value.tenant
							? [...byCustomer.entries()].map(([customer, list]) => (
									<optgroup key={customer} label={customer}>
										{list.map((c) => (
											<option key={c.id} value={c.id}>
												{c.name}
											</option>
										))}
									</optgroup>
								))
							: kbs.map((c) => (
									<option key={c.id} value={c.id}>
										{c.name}
									</option>
								))}
					</NativeSelect>
				</div>
				{admin && hasOwn ? (
					<div className="flex h-9 items-center gap-2 self-end">
						<Checkbox
							id={`${id}-own`}
							checked={!!value.excludeOwn}
							onCheckedChange={(v) => onChange({ own: v === true ? 0 : undefined })}
						/>
						<Label htmlFor={`${id}-own`} className="font-normal">
							{t.own.toggle}
						</Label>
					</div>
				) : null}
				<div className="grid gap-1.5">
					<span className="text-sm leading-none font-medium" id={`${id}-group`}>
						{t.group}
					</span>
					<Segmented<UsageGroup>
						label={t.group}
						value={value.group}
						onChange={(g) => onChange({ group: g })}
						options={groups.map((g) => ({ value: g, label: t.groups[g] }))}
					/>
				</div>
			</div>
			{problem ? (
				<p id={`${id}-problem`} role="alert" className="m-0 text-[13px] font-semibold text-destructive">
					{t.rangeProblems[problem]}
				</p>
			) : null}
		</section>
	);
}
