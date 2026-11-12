import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { useId } from "react";
import { Field } from "@/components/auth/Field";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { Segmented } from "@/components/patterns/Segmented";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import type { LeadsSearch } from "@/lib/search";
import { leadsStrings } from "@/lib/strings/leads";
import { HandoffDrawer } from "./HandoffDrawer";
import { HandoffsTable } from "./HandoffsTable";
import { HANDOFFS_PAGE, type HandoffStatus, useHandoffs } from "./handoffs-api";
import { ReferenceLookup } from "./ReferenceLookup"; // TV2 reference

const t = leadsStrings.handoffs;
const route = getRouteApi("/_app/leads");

/** What a change in the filter row sets in the URL (`undefined` clears one). */
type Patch = Partial<Pick<LeadsSearch, "kb" | "hstatus" | "hreason" | "hchannel" | "from" | "to">>;

/** `/leads?tab=handoffs`: who was offered a person, how and why. */
export function HandoffsTab() {
	const search = route.useSearch();
	const navigate = route.useNavigate();
	const kbs = useQuery(corporaSwitcherQuery);
	const ids = { kb: useId(), reason: useId(), channel: useId(), from: useId(), to: useId() };

	const page = search.page ?? 1;
	const filtered = !!(search.kb || search.hstatus || search.hreason || search.hchannel || search.from || search.to);
	const q = useHandoffs({
		corpus_id: search.kb,
		status: search.hstatus,
		reason: search.hreason,
		channel: search.hchannel,
		from: search.from,
		to: search.to,
		offset: (page - 1) * HANDOFFS_PAGE,
		limit: HANDOFFS_PAGE,
	});

	const change = (patch: Patch) => void navigate({ search: (prev) => ({ ...prev, ...patch, page: undefined }) });
	const open = (id: string | undefined) => void navigate({ search: (prev) => ({ ...prev, handoff: id }) });
	const firstKb = kbs.data?.items[0]?.id;

	return (
		<div className="grid gap-4">
			{/* TV2 reference */}
			<ReferenceLookup
				onOpenHandoff={open}
				onOpenLead={(lead) =>
					void navigate({ search: (prev) => ({ ...prev, tab: "leads", lead, handoff: undefined }) })
				}
			/>
			<search aria-label={t.filtersLabel} className="grid gap-3 rounded-lg border border-border bg-card p-3.5">
				<div className="flex flex-wrap items-end gap-3">
					<Field id={ids.kb} label={t.kb}>
						<NativeSelect
							id={ids.kb}
							value={search.kb ?? ""}
							onChange={(e) => change({ kb: e.target.value || undefined })}
						>
							<option value="">{t.allKbs}</option>
							{(kbs.data?.items ?? []).map((k) => (
								<option key={k.id} value={k.id}>
									{k.name}
								</option>
							))}
						</NativeSelect>
					</Field>
					<Segmented<HandoffStatus | "all">
						label={t.status}
						value={search.hstatus ?? "all"}
						options={[
							{ value: "all", label: t.statusAll },
							{ value: "new", label: t.statusNew },
							{ value: "handled", label: t.statusHandled },
						]}
						onChange={(v) => change({ hstatus: v === "all" ? undefined : v })}
					/>
				</div>
				<div className="flex flex-wrap items-end gap-3">
					<Field id={ids.reason} label={t.reason}>
						<NativeSelect
							id={ids.reason}
							value={search.hreason ?? ""}
							onChange={(e) => change({ hreason: e.target.value || undefined })}
						>
							<option value="">{t.allReasons}</option>
							{Object.entries(t.reasons).map(([k, label]) => (
								<option key={k} value={k}>
									{label}
								</option>
							))}
						</NativeSelect>
					</Field>
					<Field id={ids.channel} label={t.channel}>
						<NativeSelect
							id={ids.channel}
							value={search.hchannel ?? ""}
							onChange={(e) => change({ hchannel: e.target.value || undefined })}
						>
							<option value="">{t.allChannels}</option>
							{Object.entries(t.channels).map(([k, label]) => (
								<option key={k} value={k}>
									{label}
								</option>
							))}
						</NativeSelect>
					</Field>
					<Field id={ids.from} label={leadsStrings.filters.from}>
						<Input
							id={ids.from}
							type="date"
							value={search.from ?? ""}
							max={search.to}
							onChange={(e) => change({ from: e.target.value || undefined })}
						/>
					</Field>
					<Field id={ids.to} label={leadsStrings.filters.to}>
						<Input
							id={ids.to}
							type="date"
							value={search.to ?? ""}
							min={search.from}
							onChange={(e) => change({ to: e.target.value || undefined })}
						/>
					</Field>
					{filtered ? (
						<Button
							variant="ghost"
							onClick={() => void navigate({ search: (prev) => ({ tab: prev.tab, handoff: prev.handoff }) })}
						>
							{t.clear}
						</Button>
					) : null}
				</div>
			</search>
			{q.isPending ? (
				<TableSkeleton rows={6} />
			) : q.isError ? (
				<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
			) : q.data.total === 0 && !filtered ? (
				<EmptyState
					title={t.emptyTitle}
					body={t.emptyBody}
					primaryAction={
						firstKb ? (
							<Link
								to="/kbs/$kbId"
								params={{ kbId: search.kb ?? firstKb }}
								search={{ tab: "assistant", section: "handoff" }}
								className={buttonVariants({ variant: "default" })}
							>
								{t.settingsLink}
							</Link>
						) : undefined
					}
				/>
			) : (
				<div className="overflow-hidden rounded-lg border border-border bg-card">
					{q.data.items.length === 0 ? (
						<p className="m-0 p-6 text-center text-[13.5px] text-muted-foreground">{t.emptyFiltered}</p>
					) : (
						<HandoffsTable rows={q.data.items} onOpen={open} />
					)}
					<Pager
						page={page}
						pageSize={HANDOFFS_PAGE}
						total={q.data.total}
						onPage={(p) => void navigate({ search: (prev) => ({ ...prev, page: p === 1 ? undefined : p }) })}
					/>
				</div>
			)}
			<HandoffDrawer id={search.handoff} onClose={() => open(undefined)} />
		</div>
	);
}
