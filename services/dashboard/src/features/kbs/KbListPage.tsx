import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { FileUp, Globe, Info, Plus, Rows3 } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { NotAvailable } from "@/components/shell/NotAvailable";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ADD_CUSTOMER, FirstCustomerPrompt, useCustomers } from "@/features/customers/first-customer";
import { isApiError } from "@/lib/api/errors";
import type { Corpus } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { tenantLabel } from "@/lib/format/tenant";
import { en, fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { activeTenantsQuery, corporaQuery, KB_PAGE_SIZE, planMeQuery, useKbUsage7d } from "./api";
import { CreateKbDialog } from "./CreateKbDialog";
import { KbAvatar } from "./KbAvatar";

const route = getRouteApi("/_app/kbs/");

/** `/kbs` (mockup `[data-view=kbs]`). */
export function KbListPage() {
	const principal = usePrincipal();
	const isAdmin = principal?.view === "admin";
	const { page = 1, tenant } = route.useSearch();
	const navigate = useNavigate();
	const [creating, setCreating] = useState(false);
	const customers = useCustomers();
	const allTenants = useQuery({ ...activeTenantsQuery, enabled: isAdmin });
	const { new: openNew } = route.useSearch();
	// `?new=1` (the getting-started list) opens the dialog once, then leaves the URL clean.
	useEffect(() => {
		if (!openNew) return;
		setCreating(true);
		void navigate({ to: "/kbs", search: (prev) => ({ ...prev, new: undefined }), replace: true });
	}, [openNew, navigate]);

	const tenantId = isAdmin ? tenant : undefined;
	const list = useQuery(corporaQuery({ tenant_id: tenantId, offset: (page - 1) * KB_PAGE_SIZE, limit: KB_PAGE_SIZE }));
	const usage = useKbUsage7d(tenantId);
	const questions = useMemo(() => {
		const m = new Map<string, number>();
		for (const r of usage.data?.rows ?? []) if (r.key) m.set(r.key, r.requests);
		return m;
	}, [usage.data]);

	if (list.isError && isApiError(list.error) && list.error.status === 403) return <NotAvailable />;

	const columns: Column<Corpus>[] = [
		{
			id: "name",
			header: t.list.colName,
			sortValue: (k) => k.name,
			cell: (k) => (
				<div className="flex min-w-0 items-center gap-2.5">
					<KbAvatar kb={k} />
					<div className="min-w-0">
						<Link
							to="/kbs/$kbId"
							params={{ kbId: k.id }}
							className="block truncate font-semibold text-foreground no-underline hover:underline"
						>
							{k.name}
						</Link>
						{k.description ? <small className="block truncate text-muted-foreground">{k.description}</small> : null}
					</div>
				</div>
			),
		},
		...(isAdmin
			? [
					{
						id: "customer",
						header: t.list.colCustomer,
						sortValue: (k: Corpus) => k.tenant_name,
						cell: (k: Corpus) => k.tenant_name,
						stackLabel: true,
					},
				]
			: []),
		{
			id: "documents",
			header: t.list.colDocuments,
			align: "right",
			sortValue: (k) => k.document_count,
			cell: (k) => formatNumber(k.document_count),
			stackLabel: true,
		},
		{
			id: "chunks",
			header: t.list.colChunks,
			align: "right",
			sortValue: (k) => k.chunk_count,
			cell: (k) => formatNumber(k.chunk_count),
			stackLabel: true,
		},
		{
			id: "questions",
			header: t.list.colQuestions,
			align: "right",
			sortValue: (k) => questions.get(k.id) ?? 0,
			cell: (k) => (usage.isError || usage.isPending ? "—" : formatNumber(questions.get(k.id) ?? 0)),
			stackLabel: true,
		},
		{
			id: "activity",
			header: t.list.colActivity,
			sortValue: (k) => k.last_activity_at ?? "",
			cell: (k) => (
				<span title={k.last_activity_at ?? undefined}>
					{k.last_activity_at ? formatRelative(k.last_activity_at) : t.list.never}
				</span>
			),
			stackLabel: true,
		},
		{
			id: "status",
			header: t.list.colStatus,
			cell: (k) => <KbStatus kb={k} />,
		},
	];

	const rows = list.data?.items ?? [];
	const total = list.data?.total ?? 0;
	// A knowledge base belongs to a customer: with none yet, "New knowledge base" starts at "Add a customer".
	const newKb = () => {
		if (customers.none) void navigate({ ...ADD_CUSTOMER });
		else setCreating(true);
	};
	const filteredName = tenant ? allTenants.data?.items.find((x) => x.id === tenant)?.name : undefined;
	const setSearch = (next: { page?: number; tenant?: string }) =>
		void navigate({ to: "/kbs", search: (prev) => ({ ...prev, ...next }) });

	return (
		<>
			<PageHeader
				title={t.list.title}
				description={
					isAdmin ? t.list.leadAdmin : fill(t.list.leadTenant, { tenant: principal?.tenant?.name ?? "Your workspace" })
				}
				actions={
					<OwnerGate action="kb.create">
						<Button variant="default" onClick={newKb}>
							<Plus aria-hidden />
							{t.list.create}
						</Button>
					</OwnerGate>
				}
			/>
			{isAdmin ? <CustomerFilter value={tenant} onChange={(v) => setSearch({ tenant: v, page: undefined })} /> : null}
			{list.isPending ? (
				<div className="rounded-lg border border-border bg-card">
					<TableSkeleton rows={4} />
				</div>
			) : list.isError ? (
				<ErrorState error={list.error} onRetry={() => void list.refetch()} />
			) : rows.length === 0 && page === 1 && customers.none && !tenant ? (
				<FirstCustomerPrompt />
			) : rows.length === 0 && page === 1 ? (
				<EmptyState
					title={
						tenant && filteredName
							? fill(t.list.emptyFilteredTitle, { customer: filteredName })
							: tenant
								? t.list.emptyFilteredFallback
								: t.list.emptyTitle
					}
					body={
						tenant && filteredName
							? fill(t.list.emptyFilteredBody, { customer: filteredName })
							: tenant
								? t.list.emptyFilteredFallbackBody
								: t.list.emptyBody
					}
					primaryAction={
						<OwnerGate action="kb.create">
							<Button variant="default" onClick={newKb}>
								<Plus aria-hidden />
								{tenant && filteredName ? fill(t.list.createFor, { customer: filteredName }) : t.list.create}
							</Button>
						</OwnerGate>
					}
					art={<Ways />}
				/>
			) : (
				<div className="overflow-x-auto rounded-lg border border-border bg-card">
					<DataTable
						caption={t.list.caption}
						columns={columns}
						rows={rows}
						rowKey={(k) => k.id}
						onRowClick={(k) => void navigate({ to: "/kbs/$kbId", params: { kbId: k.id } })}
					/>
					{total > KB_PAGE_SIZE ? (
						<Pager page={page} pageSize={KB_PAGE_SIZE} total={total} onPage={(p) => setSearch({ page: p })} />
					) : null}
					{!isAdmin ? <PlanNote /> : null}
				</div>
			)}
			<CreateKbDialog open={creating} onOpenChange={setCreating} defaultTenantId={tenant} />
		</>
	);
}

export function KbStatus({ kb }: { kb: Pick<Corpus, "document_count"> }) {
	return kb.document_count > 0 ? (
		<Badge variant="ok">{t.list.live}</Badge>
	) : (
		<Badge variant="warn">{t.list.needsContent}</Badge>
	);
}

function CustomerFilter({ value, onChange }: { value?: string; onChange: (v: string | undefined) => void }) {
	const id = useId();
	const tenants = useQuery(activeTenantsQuery);
	return (
		<div className="flex flex-wrap items-center gap-2.5">
			<label htmlFor={id} className="text-[13px] font-semibold">
				{t.list.customerFilter}
			</label>
			<NativeSelect
				id={id}
				className="w-64 max-w-full"
				value={value ?? ""}
				disabled={tenants.isError}
				onChange={(e) => onChange(e.target.value || undefined)}
			>
				<option value="">{t.list.allCustomers}</option>
				{(tenants.data?.items ?? []).map((x) => (
					<option key={x.id} value={x.id}>
						{tenantLabel(x)}
					</option>
				))}
			</NativeSelect>
			{tenants.isError ? (
				<Button size="sm" variant="ghost" onClick={() => void tenants.refetch()}>
					{t.create.customersError} {en.common.retry}
				</Button>
			) : null}
		</div>
	);
}

/** "Your {plan} plan allows {n} knowledge bases; you're using {m}." — hidden when unlimited or on error. */
function PlanNote() {
	const plan = useQuery({ ...planMeQuery, retry: false });
	const limit = plan.data?.limits.max_knowledge_bases;
	if (!plan.data || limit == null) return null;
	return (
		<div className="flex items-center gap-2.5 border-t border-border px-4 py-3 text-[13px] text-foreground-2">
			<Info className="size-4 shrink-0 text-muted-foreground" aria-hidden />
			<span>
				{fill(t.list.planNote, {
					plan: plan.data.plan.name,
					limit: formatNumber(limit),
					used: formatNumber(plan.data.usage.knowledge_bases),
				})}{" "}
				<Link to="/plan" className="font-semibold text-brand-text">
					{t.list.planLink}
				</Link>
			</span>
		</div>
	);
}

function Ways() {
	const icons = [FileUp, Globe, Rows3];
	return (
		<ul className="m-0 grid list-none gap-2.5 p-0">
			{t.list.ways.map((w, i) => {
				const Icon = icons[i] ?? FileUp;
				return (
					<li key={w.b} className="flex items-center gap-3 rounded-md border border-border bg-secondary p-3">
						<Icon className="size-5 text-primary" aria-hidden />
						<div>
							<b className="block text-[13.5px]">{w.b}</b>
							<span className="text-[12.5px] text-muted-foreground">{w.t}</span>
						</div>
					</li>
				);
			})}
		</ul>
	);
}
