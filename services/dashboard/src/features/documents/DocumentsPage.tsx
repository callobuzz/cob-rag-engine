import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { FileUp, Globe, Rows3, Search, Upload } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { NotAvailable } from "@/components/shell/NotAvailable";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { isApiError } from "@/lib/api/errors";
import type { Corpus } from "@/lib/api/queries";
import { formatBytes } from "@/lib/format/bytes";
import { formatDate, formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { DOCUMENTS_PAGE_SIZE, type DocumentSummary, useDocuments, usePendingReviews } from "./api";
import { PendingReviewCard } from "./PendingReviewCard";
import { isSystemDocument, MimeIcon, useDebouncedValue } from "./ui";

const route = getRouteApi("/_app/documents/");

/** `/documents?kb=&q=&page=` (mockup `[data-view=documents]`). */
export function DocumentsPage() {
	return <KbScopedPage title={t.documents.title}>{(kb) => <Documents key={kb.id} kb={kb} />}</KbScopedPage>;
}

export function documentName(d: Pick<DocumentSummary, "file_name" | "external_id" | "id">): string {
	return d.file_name || d.external_id || d.id;
}

function Documents({ kb }: { kb: Corpus }) {
	const { q = "", page = 1 } = route.useSearch();
	const navigate = useNavigate();
	const list = useDocuments(kb.id, {
		search: q || undefined,
		offset: (page - 1) * DOCUMENTS_PAGE_SIZE,
		limit: DOCUMENTS_PAGE_SIZE,
	});
	const setSearch = (next: { q?: string; page?: number }) =>
		void navigate({
			to: "/documents",
			search: { kb: kb.id, q: q || undefined, page: page > 1 ? page : undefined, ...next },
			replace: true,
		});

	if (list.isError && isApiError(list.error) && list.error.status === 403) return <NotAvailable />;

	const rows = list.data?.items ?? [];
	const count = list.data?.count ?? 0;
	const emptyKb = !q && page === 1 && list.isSuccess && count === 0;

	const columns: Column<DocumentSummary>[] = [
		{
			id: "name",
			header: t.documents.colDocument,
			sortValue: (d) => documentName(d),
			cell: (d) => (
				<div className="flex min-w-0 items-center gap-2.5">
					<MimeIcon mime={d.mime_type} isRecord={!!d.external_id} />
					<div className="min-w-0">
						<Link
							to="/documents/$documentId"
							params={{ documentId: d.id }}
							search={{ kb: kb.id }}
							className="block max-w-[420px] truncate font-semibold text-foreground no-underline hover:underline"
						>
							{documentName(d)}
						</Link>
						{isSystemDocument(d) ? (
							<Badge variant="info" className="mt-0.5">
								{t.system.badge}
							</Badge>
						) : null}
						{d.file_name && d.external_id ? (
							<small className="block truncate font-mono text-muted-foreground">{d.external_id}</small>
						) : null}
					</div>
				</div>
			),
		},
		{
			id: "source",
			header: t.documents.colSource,
			sortValue: (d) => (d.external_id ? t.common.record : t.common.upload),
			cell: (d) => (d.external_id ? t.common.record : t.common.upload),
			stackLabel: true,
		},
		{
			id: "versions",
			header: t.documents.colVersions,
			align: "right",
			sortValue: (d) => d.version_count ?? 0,
			cell: (d) => formatNumber(d.version_count ?? 0),
			stackLabel: true,
		},
		{
			id: "size",
			header: t.documents.colSize,
			align: "right",
			sortValue: (d) => d.size_bytes ?? 0,
			cell: (d) => formatBytes(d.size_bytes),
			stackLabel: true,
		},
		{
			id: "added",
			header: t.documents.colAdded,
			sortValue: (d) => d.ingested_at,
			cell: (d) => <span title={formatDate(d.ingested_at)}>{formatRelative(d.ingested_at)}</span>,
			stackLabel: true,
		},
		{
			id: "status",
			header: t.documents.colStatus,
			cell: (d) =>
				d.active_version_id ? (
					<Badge variant="ok">{t.documents.live}</Badge>
				) : (
					<Badge variant="warn">{t.documents.noActive}</Badge>
				),
		},
	];

	return (
		<>
			<PageHeader
				title={t.documents.title}
				description={
					list.isSuccess && !q
						? fill(count === 1 ? t.documents.leadOne : t.documents.lead, { kb: kb.name, n: formatNumber(count) })
						: kb.name
				}
				actions={
					<Link to="/add" search={{ kb: kb.id }} className={buttonVariants({ variant: "default" })}>
						<Upload aria-hidden />
						{t.documents.addContent}
					</Link>
				}
			/>
			<PendingStrip kb={kb.id} />
			{emptyKb ? (
				<EmptyKb kb={kb} />
			) : (
				<>
					<SearchBox value={q} onChange={(v) => setSearch({ q: v || undefined, page: undefined })} />
					{list.isPending ? (
						<div className="rounded-lg border border-border bg-card">
							<TableSkeleton rows={5} />
						</div>
					) : list.isError ? (
						<ErrorState error={list.error} onRetry={() => void list.refetch()} />
					) : rows.length === 0 && q ? (
						<div className="grid justify-items-start gap-2 rounded-lg border border-border bg-card p-6">
							<h2 className="text-[17px] font-bold">{fill(t.documents.noMatchesTitle, { q })}</h2>
							<p className="m-0 text-foreground-2">{t.documents.noMatchesBody}</p>
							<Button size="sm" variant="outline" onClick={() => setSearch({ q: undefined, page: undefined })}>
								{t.documents.clearSearch}
							</Button>
						</div>
					) : (
						<div className="overflow-x-auto rounded-lg border border-border bg-card">
							<DataTable
								caption={t.documents.caption}
								columns={columns}
								rows={rows}
								rowKey={(d) => d.id}
								onRowClick={(d) =>
									void navigate({ to: "/documents/$documentId", params: { documentId: d.id }, search: { kb: kb.id } })
								}
							/>
							{count > DOCUMENTS_PAGE_SIZE ? (
								<Pager
									page={page}
									pageSize={DOCUMENTS_PAGE_SIZE}
									total={count}
									onPage={(p) => setSearch({ page: p })}
								/>
							) : null}
						</div>
					)}
				</>
			)}
		</>
	);
}

/** Debounced (300 ms) search box; the URL's `?q=` is the source of truth. */
function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
	const id = useId();
	const [text, setText] = useState(value);
	const debounced = useDebouncedValue(text, 300);
	// The latest URL value and callback, read when the debounced text settles (not triggers).
	const latest = useRef({ value, onChange });
	latest.current = { value, onChange };
	useEffect(() => {
		const next = debounced.trim();
		if (next !== latest.current.value) latest.current.onChange(next);
	}, [debounced]);
	return (
		<div className="relative max-w-[420px]">
			<label htmlFor={id} className="sr-only">
				{t.documents.search}
			</label>
			<Search
				className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
				aria-hidden
			/>
			<Input
				id={id}
				type="search"
				className="pl-9"
				placeholder={t.documents.searchPlaceholder}
				value={text}
				maxLength={200}
				onChange={(e) => setText(e.target.value)}
			/>
		</div>
	);
}

function PendingStrip({ kb }: { kb: string }) {
	const q = usePendingReviews(kb);
	const jobs = q.data?.items ?? [];
	if (!jobs.length) return null;
	return (
		<section aria-labelledby="needs-review" className="grid gap-3">
			<div>
				<h2 id="needs-review" className="flex items-center gap-2 text-[17px] font-bold">
					{t.documents.reviewTitle}
					<Badge variant="warn">{formatNumber(jobs.length)}</Badge>
				</h2>
				<p className="m-0 text-[13px] text-foreground-2">{t.documents.reviewLead}</p>
			</div>
			<div className="grid gap-3 min-[861px]:grid-cols-2">
				{jobs.map((j) => (
					<PendingReviewCard key={j.id} job={j} kb={kb} />
				))}
			</div>
		</section>
	);
}

function EmptyKb({ kb }: { kb: Corpus }) {
	const icons = [FileUp, Globe, Rows3];
	return (
		<EmptyState
			title={fill(t.documents.emptyTitle, { kb: kb.name })}
			body={
				<>
					<Badge variant="info" className="mb-3 flex">
						{t.documents.emptyBadge}
					</Badge>
					{t.documents.emptyBody}
				</>
			}
			primaryAction={
				<Link to="/add" search={{ kb: kb.id }} className={buttonVariants({ variant: "default" })}>
					<Upload aria-hidden />
					{t.documents.emptyUpload}
				</Link>
			}
			secondaryAction={
				<Link to="/crawler" className={buttonVariants({ variant: "outline" })}>
					<Globe aria-hidden />
					{t.documents.emptyCrawl}
				</Link>
			}
			art={
				<ul className="m-0 grid list-none gap-2.5 p-0">
					{t.documents.ways.map((w, i) => {
						const Icon = icons[i] ?? FileUp;
						const to = i === 1 ? "/crawler" : "/add";
						const search = i === 1 ? undefined : { kb: kb.id, tab: i === 2 ? ("records" as const) : undefined };
						return (
							<li key={w.b}>
								<Link
									to={to}
									search={search}
									className="flex items-center gap-3 rounded-md border border-border bg-secondary p-3 text-foreground no-underline hover:bg-accent"
								>
									<Icon className="size-5 text-primary" aria-hidden />
									<div>
										<b className="block text-[13.5px]">{w.b}</b>
										<span className="text-[12.5px] text-muted-foreground">{w.t}</span>
									</div>
								</Link>
							</li>
						);
					})}
				</ul>
			}
		/>
	);
}
