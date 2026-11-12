import { Link, useNavigate } from "@tanstack/react-router";
import { Layers } from "lucide-react";
import { useId } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import type { Corpus } from "@/lib/api/queries";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { useWorkspaces, WORKSPACE_STATUSES, WORKSPACES_PAGE_SIZE, type Workspace } from "./api";
import { KindBadge, type PortalSearch, WorkspaceStatusBadge } from "./ui";

const count = (n: number | null | undefined) => (n == null ? "—" : formatNumber(n));

/** Workspaces of one knowledge base (`GET /portal/workspaces?corpus_id=&status=`), paged 20. */
export function WorkspaceList({
	kb,
	status,
	page,
	onStatus,
	onPage,
	onCreate,
}: {
	kb: Corpus;
	status: PortalSearch["status"];
	page: number;
	onStatus: (s: PortalSearch["status"]) => void;
	onPage: (p: number) => void;
	onCreate: () => void;
}) {
	const q = useWorkspaces(kb.id, { status, page });
	const navigate = useNavigate();
	const filterId = useId();

	const columns: Column<Workspace>[] = [
		{
			id: "title",
			header: t.list.colTitle,
			rowHeader: true,
			sortValue: (w) => w.title.toLowerCase(),
			cell: (w) => (
				<Link
					to="/portal/$workspaceId"
					params={{ workspaceId: w.id }}
					search={{ kb: kb.id }}
					aria-label={fill(t.list.open, { title: w.title })}
					className="font-semibold text-foreground no-underline hover:text-primary"
				>
					{w.title}
				</Link>
			),
		},
		{ id: "kind", header: t.list.colKind, stackLabel: true, cell: (w) => <KindBadge kind={w.kind ?? "standard"} /> },
		{
			id: "status",
			header: t.list.colStatus,
			stackLabel: true,
			sortValue: (w) => w.status,
			cell: (w) => <WorkspaceStatusBadge status={w.status} />,
		},
		{
			id: "content",
			header: t.list.colContent,
			align: "right",
			stackLabel: true,
			cell: (w) => <span className="tabular-nums">{count(w.content_count)}</span>,
		},
		{
			id: "chunks",
			header: t.list.colChunks,
			align: "right",
			stackLabel: true,
			cell: (w) => (
				<span className="tabular-nums">
					{w.chunk_count == null ? "—" : `${count(w.approved_chunk_count)} / ${count(w.chunk_count)}`}
				</span>
			),
		},
		{
			id: "locked",
			header: t.list.colLocked,
			stackLabel: true,
			cell: (w) => <span className="break-all">{w.locked_by ?? t.list.nobody}</span>,
		},
		{
			id: "updated",
			header: t.list.colUpdated,
			stackLabel: true,
			sortValue: (w) => w.updated_at,
			cell: (w) => formatDateTime(w.updated_at),
		},
	];

	const filter = (
		<div className="flex flex-wrap items-center gap-2.5">
			<label htmlFor={filterId} className="text-[13px] font-semibold">
				{t.list.statusFilter}
			</label>
			<NativeSelect
				id={filterId}
				className="w-[200px]"
				value={status ?? ""}
				onChange={(e) => onStatus((e.target.value || undefined) as PortalSearch["status"])}
			>
				<option value="">{t.list.allStatuses}</option>
				{WORKSPACE_STATUSES.map((s) => (
					<option key={s} value={s}>
						{t.status[s]}
					</option>
				))}
			</NativeSelect>
		</div>
	);

	if (q.isPending) {
		return (
			<div className="rounded-lg border border-border bg-card">
				<TableSkeleton rows={3} />
			</div>
		);
	}
	if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	const data = q.data;
	if (data.total === 0 && !status) {
		return (
			<EmptyState
				title={fill(t.list.emptyTitle, { kb: kb.name })}
				body={t.list.emptyBody}
				primaryAction={
					<Button variant="default" onClick={onCreate}>
						<Layers aria-hidden />
						{t.home.newWorkspace}
					</Button>
				}
				art={
					<ul className="m-0 grid list-none gap-2.5 p-0">
						{t.list.emptyWays.map((w) => (
							<li key={w.b} className="grid gap-0.5 rounded-md border border-border bg-muted px-4 py-3">
								<b className="text-[13.5px]">{w.b}</b>
								<span className="text-[12.5px] text-foreground-2">{w.t}</span>
							</li>
						))}
					</ul>
				}
			/>
		);
	}
	return (
		<div className="grid gap-3">
			{filter}
			<div className="overflow-x-auto rounded-lg border border-border bg-card">
				<DataTable
					caption={t.list.caption}
					columns={columns}
					rows={data.items}
					rowKey={(w) => w.id}
					empty={t.list.emptyFiltered}
					onRowClick={(w) =>
						void navigate({ to: "/portal/$workspaceId", params: { workspaceId: w.id }, search: { kb: kb.id } })
					}
				/>
				{data.total > WORKSPACES_PAGE_SIZE ? (
					<Pager page={page} pageSize={WORKSPACES_PAGE_SIZE} total={data.total} onPage={onPage} />
				) : null}
			</div>
		</div>
	);
}
