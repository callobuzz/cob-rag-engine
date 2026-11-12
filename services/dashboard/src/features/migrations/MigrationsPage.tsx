import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { CheckCheck } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { formatDateTime } from "@/lib/format/date";
import { migrationsStrings as t } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { type Migration, migrationsQuery, pendingCountQuery, useApproveAll } from "./api";
import { MigrationDetail } from "./MigrationDetail";
import { MigrationProgress, MigrationStatusBadge, modelOf, strategyLabel } from "./MigrationRow";
import { MIGRATION_STATUSES, MIGRATIONS_PAGE_SIZE, type MigrationsSearch } from "./search";

const route = getRouteApi("/_app/migrations");

/**
 * `/migrations` — every migration the caller may see (admin all, a tenant its own KBs), filtered
 * by KB and status, polling while one moves. Admin: approve, cancel, retry, roll back, approve all.
 * Tenants read only.
 */
export function MigrationsPage() {
	const search = route.useSearch();
	const navigate = useNavigate();
	const principal = usePrincipal();
	const isAdmin = principal?.view === "admin";
	const ids = { kb: useId(), status: useId() };
	const list = useQuery(migrationsQuery(search));
	const kbs = useQuery(corporaSwitcherQuery);
	const pending = useQuery({ ...pendingCountQuery, enabled: isAdmin });
	const approveAll = useApproveAll();
	const [confirmAll, setConfirmAll] = useState(false);
	const [allError, setAllError] = useState<string | null>(null);
	const page = search.page ?? 1;

	const names = useMemo(() => new Map((kbs.data?.items ?? []).map((k) => [k.id, k.name])), [kbs.data]);
	const kbName = (id: string) => names.get(id) ?? t.unknownKb;
	const kbOptions = [...(kbs.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name, "en-IN"));
	const rows = list.data?.items ?? [];
	const summary = rows.find((r) => r.id === search.id) ?? null;

	const setSearch = (next: Partial<MigrationsSearch>, reset = true) =>
		void navigate({
			to: "/migrations",
			search: { ...search, ...(reset ? { page: undefined } : {}), ...next },
			replace: true,
		});

	const doApproveAll = async () => {
		setAllError(null);
		try {
			const r = await approveAll.mutateAsync();
			setConfirmAll(false);
			if (r.count === 0) toast(t.page.noneApproved);
			else toast.success(r.count === 1 ? t.page.approvedAllOne : fill(t.page.approvedAll, { n: r.count }));
		} catch (e) {
			setAllError(messageFor(toApiError(e)).title);
		}
	};

	const columns: Column<Migration>[] = [
		{
			id: "kb",
			header: t.cols.kb,
			rowHeader: true,
			cell: (m) => (
				<Button
					variant="link"
					className="text-left font-semibold whitespace-normal"
					aria-label={fill(t.openRow, { kb: kbName(m.corpus_id) })}
					onClick={() => setSearch({ id: m.id }, false)}
				>
					{kbName(m.corpus_id)}
				</Button>
			),
		},
		{
			id: "models",
			header: t.cols.models,
			stackLabel: true,
			cell: (m) => (
				<span className="font-mono text-[12px] break-all">
					{modelOf(m.from_config)} → {modelOf(m.to_config)}
				</span>
			),
		},
		{ id: "strategy", header: t.cols.strategy, stackLabel: true, cell: (m) => strategyLabel(m.strategy) },
		{ id: "status", header: t.cols.status, cell: (m) => <MigrationStatusBadge status={m.status} /> },
		{ id: "progress", header: t.cols.progress, cell: (m) => <MigrationProgress m={m} /> },
		{
			id: "created",
			header: t.cols.created,
			stackLabel: true,
			sortValue: (m) => m.created_at ?? "",
			cell: (m) => <span className="whitespace-nowrap">{formatDateTime(m.created_at)}</span>,
		},
	];

	const pendingCount = pending.data ?? 0;
	const filtered = !!(search.kb || search.status);

	return (
		<div className="grid gap-4">
			<PageHeader
				title={t.page.title}
				description={t.page.lead}
				actions={
					isAdmin && pendingCount > 0 ? (
						<Button
							variant="default"
							onClick={() => {
								setAllError(null);
								setConfirmAll(true);
							}}
						>
							<CheckCheck aria-hidden />
							{t.page.approveAll} ({pendingCount})
						</Button>
					) : undefined
				}
			/>
			{isAdmin ? null : <FormAlert tone="info">{t.page.readOnly}</FormAlert>}
			<div className="grid gap-3 rounded-lg border border-border bg-card p-3 min-[861px]:grid-cols-2 min-[1101px]:grid-cols-[minmax(0,320px)_minmax(0,240px)]">
				<div className="grid gap-1">
					<label htmlFor={ids.kb} className="text-[12.5px] font-semibold text-foreground-2">
						{t.filters.kb}
					</label>
					<NativeSelect
						id={ids.kb}
						value={search.kb ?? ""}
						onChange={(e) => setSearch({ kb: e.target.value || undefined })}
					>
						<option value="">{t.filters.allKbs}</option>
						{kbOptions.map((k) => (
							<option key={k.id} value={k.id}>
								{isAdmin && k.tenant_name ? `${k.name} · ${k.tenant_name}` : k.name}
							</option>
						))}
					</NativeSelect>
				</div>
				<div className="grid gap-1">
					<label htmlFor={ids.status} className="text-[12.5px] font-semibold text-foreground-2">
						{t.filters.status}
					</label>
					<NativeSelect
						id={ids.status}
						value={search.status ?? ""}
						onChange={(e) => setSearch({ status: (e.target.value || undefined) as MigrationsSearch["status"] })}
					>
						<option value="">{t.filters.allStatuses}</option>
						{MIGRATION_STATUSES.map((s) => (
							<option key={s} value={s}>
								{t.status[s]}
							</option>
						))}
					</NativeSelect>
				</div>
			</div>
			{list.isPending ? (
				<div className="rounded-lg border border-border bg-card">
					<TableSkeleton rows={4} />
				</div>
			) : list.isError ? (
				<ErrorState error={list.error} title={t.page.error} onRetry={() => void list.refetch()} />
			) : rows.length === 0 && !filtered && page === 1 ? (
				<EmptyState title={t.page.empty} body={t.page.emptyBody} />
			) : (
				<div className="overflow-x-auto rounded-lg border border-border bg-card">
					<DataTable
						caption={t.page.caption}
						columns={columns}
						rows={rows}
						rowKey={(m) => m.id}
						empty={t.page.empty}
						onRowClick={(m) => setSearch({ id: m.id }, false)}
					/>
					{(list.data?.total ?? 0) > MIGRATIONS_PAGE_SIZE ? (
						<Pager
							page={page}
							pageSize={MIGRATIONS_PAGE_SIZE}
							total={list.data?.total ?? 0}
							onPage={(p) => setSearch({ page: p > 1 ? p : undefined }, false)}
						/>
					) : null}
				</div>
			)}
			<MigrationDetail
				id={search.id}
				summary={summary}
				kbName={kbName}
				isAdmin={isAdmin}
				onClose={() => setSearch({ id: undefined }, false)}
			/>
			<ConfirmDialog
				open={confirmAll}
				onOpenChange={setConfirmAll}
				title={pendingCount === 1 ? t.page.approveAllTitleOne : fill(t.page.approveAllTitle, { n: pendingCount })}
				body={t.page.approveAllBody}
				confirmLabel={t.page.approveAll}
				destructive={false}
				pending={approveAll.isPending}
				onConfirm={() => void doApproveAll()}
			>
				{allError ? <FormAlert>{allError}</FormAlert> : null}
			</ConfirmDialog>
		</div>
	);
}
