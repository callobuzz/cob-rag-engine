import { type Column, DataTable } from "@/components/patterns/DataTable";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { ScrapePage } from "@/lib/api/scraper-types";
import { formatNumber } from "@/lib/format/number";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { fill } from "@/lib/strings/en";
import { pageBadge, pageStatusLabel, siteLabel } from "./statuses";

export function pageTitle(p: Pick<ScrapePage, "title" | "url">): string {
	return p.title.trim() || siteLabel(p.url) || t.pages.untitled;
}

export function PageStatusBadge({ status }: { status: ScrapePage["status"] }) {
	return <StatusBadge status={pageBadge(status)} label={pageStatusLabel(status)} />;
}

/**
 * Pages of a crawl: select (verified pages only — the only ones approve accepts), page, words,
 * status, and the delivery error or rejection reason. A row opens the page drawer.
 */
export function PagesTable({
	pages,
	selected,
	onToggle,
	onOpen,
	empty,
}: {
	pages: ScrapePage[];
	selected: ReadonlySet<string>;
	onToggle: (id: string, on: boolean) => void;
	onOpen: (page: ScrapePage) => void;
	empty: string;
}) {
	const columns: Column<ScrapePage>[] = [
		{
			id: "select",
			header: t.pages.colSelect,
			className: "w-10",
			cell: (p) =>
				p.status === "verified" ? (
					<Checkbox
						checked={selected.has(p.id)}
						onCheckedChange={(v) => onToggle(p.id, v === true)}
						aria-label={fill(t.pages.selectRow, { title: pageTitle(p) })}
					/>
				) : null,
		},
		{
			id: "page",
			header: t.pages.colPage,
			sortValue: (p) => pageTitle(p),
			cell: (p) => (
				<div className="grid min-w-0 max-w-[460px]">
					<Button
						variant="link"
						className="h-auto justify-start truncate p-0 font-semibold text-foreground"
						aria-label={fill(t.pages.open, { title: pageTitle(p) })}
						onClick={() => onOpen(p)}
					>
						<span className="truncate">{pageTitle(p)}</span>
					</Button>
					<small className="truncate font-mono text-muted-foreground">{siteLabel(p.url)}</small>
				</div>
			),
		},
		{
			id: "words",
			header: t.pages.colWords,
			align: "right",
			sortValue: (p) => p.word_count,
			cell: (p) => formatNumber(p.word_count),
			stackLabel: true,
		},
		{
			id: "status",
			header: t.pages.colStatus,
			sortValue: (p) => pageStatusLabel(p.status),
			cell: (p) => <PageStatusBadge status={p.status} />,
		},
		{
			id: "issue",
			header: t.pages.colIssue,
			cell: (p) =>
				p.delivery_error ? (
					<span className="text-destructive" data-testid="delivery-error">
						{p.delivery_error}
					</span>
				) : p.rejection_reason ? (
					<span className="text-foreground-2">{p.rejection_reason}</span>
				) : (
					"—"
				),
			stackLabel: true,
		},
	];
	return (
		<DataTable
			caption={t.pages.caption}
			columns={columns}
			rows={pages}
			rowKey={(p) => p.id}
			empty={empty}
			onRowClick={onOpen}
		/>
	);
}
