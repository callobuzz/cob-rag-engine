import { Link } from "@tanstack/react-router";
import { Trash2 } from "lucide-react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button, buttonVariants } from "@/components/ui/button";
import type { ScrapeJob } from "@/lib/api/scraper-types";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { fill } from "@/lib/strings/en";
import { jobBadge, jobStatusLabel, siteLabel } from "./statuses";

export function JobStatusBadge({ job }: { job: Pick<ScrapeJob, "status" | "review_mode"> }) {
	return <StatusBadge status={jobBadge(job)} label={jobStatusLabel(job)} />;
}

/** Crawls: site, status, pages done/found, started, error, View / Delete. */
export function JobsTable({
	jobs,
	kbName,
	showKb,
	onDelete,
}: {
	jobs: ScrapeJob[];
	/** Knowledge base name for a corpus id (null when unknown). */
	kbName: (id: string) => string | null;
	showKb: boolean;
	onDelete: (job: ScrapeJob) => void;
}) {
	const columns: Column<ScrapeJob>[] = [
		{
			id: "site",
			header: t.list.colSite,
			rowHeader: true,
			sortValue: (j) => siteLabel(j.url),
			cell: (j) => (
				<Link
					to="/crawler/$jobId"
					params={{ jobId: j.id }}
					className="block max-w-[360px] truncate font-semibold text-foreground no-underline hover:underline"
				>
					{siteLabel(j.url)}
				</Link>
			),
		},
		...(showKb
			? [
					{
						id: "kb",
						header: t.list.colKb,
						sortValue: (j: ScrapeJob) => kbName(j.target_corpus_id) ?? "",
						cell: (j: ScrapeJob) =>
							j.target_corpus_id ? (kbName(j.target_corpus_id) ?? t.list.unknownKb) : t.list.noKb,
						stackLabel: true,
					},
				]
			: []),
		{
			id: "status",
			header: t.list.colStatus,
			sortValue: (j) => jobStatusLabel(j),
			cell: (j) => <JobStatusBadge job={j} />,
		},
		{
			id: "pages",
			header: t.list.colPages,
			align: "right",
			sortValue: (j) => j.pages_found,
			cell: (j) => `${formatNumber(j.pages_done)} / ${formatNumber(j.pages_found)}`,
			stackLabel: true,
		},
		{
			id: "created",
			header: t.list.colCreated,
			sortValue: (j) => j.created_at,
			cell: (j) => <span title={formatDateTime(j.created_at)}>{formatRelative(j.created_at)}</span>,
			stackLabel: true,
		},
		{
			id: "error",
			header: t.list.colError,
			cell: (j) => (j.error ? <span className="text-destructive">{j.error}</span> : "—"),
			stackLabel: true,
		},
		{
			id: "actions",
			header: t.list.colActions,
			align: "right",
			cell: (j) => (
				<div className="flex justify-end gap-1.5 max-[600px]:justify-start">
					<Link
						to="/crawler/$jobId"
						params={{ jobId: j.id }}
						aria-label={fill(t.list.viewLabel, { site: siteLabel(j.url) })}
						className={buttonVariants({ variant: "outline", size: "sm" })}
					>
						{t.list.view}
					</Link>
					<Button
						variant="ghost"
						size="sm"
						aria-label={fill(t.list.deleteLabel, { site: siteLabel(j.url) })}
						onClick={() => onDelete(j)}
					>
						<Trash2 aria-hidden />
						{t.list.delete}
					</Button>
				</div>
			),
		},
	];
	return (
		<DataTable
			caption={t.list.caption}
			columns={columns}
			rows={jobs}
			rowKey={(j) => j.id}
			initialSort={{ id: "created", dir: "desc" }}
		/>
	);
}
