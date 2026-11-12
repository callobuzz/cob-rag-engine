import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { Download } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import { fill } from "@/lib/strings/en";
import { leadsStrings as t } from "@/lib/strings/leads";
import { downloadLeadsCsv, LEADS_PAGE, type LeadParams, useLeads } from "./api";
import { HandoffsTab } from "./HandoffsTab";
import { useNewHandoffCount } from "./handoffs-api";
import { LeadDrawer } from "./LeadDrawer";
import { type FilterPatch, LeadFilters } from "./LeadFilters";
import { LeadsTable } from "./LeadsTable";

const route = getRouteApi("/_app/leads");

/** The Leads tab: the filters, the list and the lead drawer. */
function LeadsList() {
	const search = route.useSearch();
	const navigate = route.useNavigate();
	const kbs = useQuery(corporaSwitcherQuery);

	const page = search.page ?? 1;
	const filters: Omit<LeadParams, "offset" | "limit"> = {
		corpus_id: search.kb,
		status: search.status,
		qualification: search.qualification,
		q: search.q,
		date_from: search.from,
		date_to: search.to,
		page_url: search.url,
	};
	const q = useLeads({ ...filters, offset: (page - 1) * LEADS_PAGE, limit: LEADS_PAGE });
	const filtered = !!(
		search.kb ||
		search.status ||
		search.qualification ||
		search.q ||
		search.from ||
		search.to ||
		search.url
	);

	const change = (patch: FilterPatch) => void navigate({ search: (prev) => ({ ...prev, ...patch, page: undefined }) });
	const open = (id: string | undefined) => void navigate({ search: (prev) => ({ ...prev, lead: id }) });

	const firstKb = kbs.data?.items[0]?.id;
	return (
		<>
			<LeadFilters
				search={search}
				kbs={kbs.data?.items ?? []}
				onChange={change}
				onClear={() =>
					void navigate({
						search: (prev) => ({ lead: prev.lead }),
					})
				}
			/>
			{q.isPending ? (
				<TableSkeleton rows={8} />
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
								search={{ tab: "assistant", section: "leads" }}
								className={buttonVariants({ variant: "default" })}
							>
								{t.emptyAction}
							</Link>
						) : undefined
					}
				/>
			) : (
				<div className="overflow-hidden rounded-lg border border-border bg-card">
					{q.data.items.length === 0 ? (
						<p className="m-0 p-6 text-center text-[13.5px] text-muted-foreground">{t.emptyFiltered}</p>
					) : (
						<LeadsTable rows={q.data.items} onOpen={open} />
					)}
					<Pager
						page={page}
						pageSize={LEADS_PAGE}
						total={q.data.total}
						onPage={(p) => void navigate({ search: (prev) => ({ ...prev, page: p === 1 ? undefined : p }) })}
					/>
				</div>
			)}
			<LeadDrawer id={search.lead} onClose={() => open(undefined)} />
		</>
	);
}

/**
 * `/leads`: the contact details visitors chose to share, and (C5) the handoffs: who was offered a
 * person. Two tabs over the knowledge bases you may see; the open tab is in the URL.
 */
export function LeadsPage() {
	const search = route.useSearch();
	const navigate = route.useNavigate();
	const [exporting, setExporting] = useState(false);
	const waiting = useNewHandoffCount(search.kb);
	const tab = search.tab ?? "leads";

	const exportCsv = async () => {
		setExporting(true);
		try {
			await downloadLeadsCsv({
				corpus_id: search.kb,
				status: search.status,
				qualification: search.qualification,
				q: search.q,
				date_from: search.from,
				date_to: search.to,
				page_url: search.url,
			});
			toast.success(t.export.done);
		} catch (e) {
			toast.error(t.export.failed, { description: messageFor(toApiError(e)).title });
		} finally {
			setExporting(false);
		}
	};

	return (
		<>
			<PageHeader
				title={t.title}
				description={t.lead}
				actions={
					tab === "leads" ? (
						<OwnerGate action="lead.export">
							<Button onClick={() => void exportCsv()} disabled={exporting}>
								<Download aria-hidden />
								{t.export.button}
							</Button>
						</OwnerGate>
					) : undefined
				}
			/>
			<Tabs
				value={tab}
				onValueChange={(v) =>
					void navigate({
						search: (prev) => ({
							kb: prev.kb,
							from: prev.from,
							to: prev.to,
							tab: v === "handoffs" ? "handoffs" : undefined,
						}),
					})
				}
			>
				<TabsList variant="line" aria-label={t.tabs.label}>
					<TabsTrigger value="leads">{t.tabs.leads}</TabsTrigger>
					<TabsTrigger value="handoffs">
						{waiting.data ? fill(t.tabs.handoffsNew, { n: waiting.data }) : t.tabs.handoffs}
					</TabsTrigger>
				</TabsList>
				<TabsContent value="leads" className="grid gap-4">
					<LeadsList />
				</TabsContent>
				<TabsContent value="handoffs" className="grid gap-4">
					<HandoffsTab />
				</TabsContent>
			</Tabs>
		</>
	);
}
