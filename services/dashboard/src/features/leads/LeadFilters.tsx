import { useEffect, useId, useState } from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Segmented } from "@/components/patterns/Segmented";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { Corpus } from "@/lib/api/queries";
import { LEAD_QUALIFICATIONS, LEAD_STATUSES, type LeadStatusFilter, type LeadsSearch } from "@/lib/search";
import { leadsStrings as t } from "@/lib/strings/leads";

/** What a change in the filter row sets in the URL (`undefined` clears one). */
export type FilterPatch = Partial<Pick<LeadsSearch, "kb" | "status" | "qualification" | "q" | "from" | "to" | "url">>;

/** A text filter that writes to the URL a moment after the person stops typing. */
function useDebounced(value: string, apply: (v: string) => void, delay = 350) {
	const [text, setText] = useState(value);
	useEffect(() => setText(value), [value]);
	useEffect(() => {
		if (text === value) return;
		const id = setTimeout(() => apply(text), delay);
		return () => clearTimeout(id);
	}, [text, value, apply, delay]);
	return [text, setText] as const;
}

export function LeadFilters({
	search,
	kbs,
	onChange,
	onClear,
}: {
	search: LeadsSearch;
	kbs: Corpus[];
	onChange: (patch: FilterPatch) => void;
	onClear: () => void;
}) {
	const ids = { kb: useId(), qual: useId(), q: useId(), from: useId(), to: useId(), url: useId() };
	const [q, setQ] = useDebounced(search.q ?? "", (v) => onChange({ q: v.trim() || undefined }));
	const [url, setUrl] = useDebounced(search.url ?? "", (v) => onChange({ url: v.trim() || undefined }));
	const active = !!(
		search.kb ||
		search.status ||
		search.qualification ||
		search.q ||
		search.from ||
		search.to ||
		search.url
	);
	return (
		<search aria-label={t.filters.label} className="grid gap-3 rounded-lg border border-border bg-card p-3.5">
			<div className="flex flex-wrap items-end gap-3">
				<Field id={ids.kb} label={t.filters.kb}>
					<NativeSelect
						id={ids.kb}
						value={search.kb ?? ""}
						onChange={(e) => onChange({ kb: e.target.value || undefined })}
					>
						<option value="">{t.filters.allKbs}</option>
						{kbs.map((k) => (
							<option key={k.id} value={k.id}>
								{k.name}
							</option>
						))}
					</NativeSelect>
				</Field>
				<Field id={ids.qual} label={t.filters.qualification}>
					<NativeSelect
						id={ids.qual}
						value={search.qualification ?? ""}
						onChange={(e) => onChange({ qualification: (e.target.value || undefined) as LeadsSearch["qualification"] })}
					>
						<option value="">{t.filters.anyQualification}</option>
						{LEAD_QUALIFICATIONS.map((v) => (
							<option key={v} value={v}>
								{t.qualification[v]}
							</option>
						))}
					</NativeSelect>
				</Field>
				<Segmented<LeadStatusFilter | "all">
					label={t.filters.status}
					value={search.status ?? "all"}
					options={[
						{ value: "all", label: t.status.all },
						...LEAD_STATUSES.map((s) => ({ value: s, label: t.status[s] })),
					]}
					onChange={(v) => onChange({ status: v === "all" ? undefined : v })}
				/>
			</div>
			<div className="flex flex-wrap items-end gap-3">
				<Field id={ids.q} label={t.filters.search} hint={t.filters.searchHint}>
					<Input
						id={ids.q}
						type="search"
						inputMode="search"
						autoComplete="off"
						maxLength={254}
						className="min-w-[220px]"
						aria-describedby={describedBy(ids.q, { hint: true })}
						value={q}
						onChange={(e) => setQ(e.target.value)}
					/>
				</Field>
				<Field id={ids.from} label={t.filters.from}>
					<Input
						id={ids.from}
						type="date"
						value={search.from ?? ""}
						max={search.to}
						onChange={(e) => onChange({ from: e.target.value || undefined })}
					/>
				</Field>
				<Field id={ids.to} label={t.filters.to}>
					<Input
						id={ids.to}
						type="date"
						value={search.to ?? ""}
						min={search.from}
						onChange={(e) => onChange({ to: e.target.value || undefined })}
					/>
				</Field>
				<Field id={ids.url} label={t.filters.pageUrl}>
					<Input
						id={ids.url}
						autoComplete="off"
						maxLength={200}
						className="min-w-[200px]"
						value={url}
						onChange={(e) => setUrl(e.target.value)}
					/>
				</Field>
				{active ? (
					<Button variant="ghost" onClick={onClear}>
						{t.filters.clear}
					</Button>
				) : null}
			</div>
		</search>
	);
}
