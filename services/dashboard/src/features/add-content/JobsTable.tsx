import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { Job } from "@/features/documents/api";
import { PendingReviewCard } from "@/features/documents/PendingReviewCard";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { JOB_STATUSES, type JobStatus } from "@/lib/search";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { JOBS_PAGE_SIZE, jobsQuery, useRetryJob } from "./api";
import { stageLabel } from "./JobStages";

const BADGE: Record<string, string> = {
	completed: "completed",
	failed: "failed",
	pending_review: "pending",
	expired: "suspended",
	skipped: "suspended",
	queued: "queued",
};

export function JobStatusBadge({ status }: { status: string }) {
	return <StatusBadge status={BADGE[status] ?? "running"} label={stageLabel(status)} />;
}

export function jobName(j: Job): string {
	return j.file_name ?? (j.mode === "records" ? t.jobs.records : j.id);
}

/** A failed upload can run again from its stored file (not a records import, which has no file, and
 * not one that a newer job already runs again). */
export const canRetry = (j: Job) => j.status === "failed" && j.mode !== "records" && !j.retried;

function RetryButton({ job, size = "sm" }: { job: Job; size?: "sm" | "default" }) {
	const retry = useRetryJob();
	return (
		<Button
			type="button"
			variant="outline"
			size={size}
			disabled={retry.isPending}
			aria-label={fill(t.jobs.retryFor, { file: jobName(job) })}
			onClick={(e) => {
				e.stopPropagation();
				retry.mutate(job.id, {
					onSuccess: () => toast.success(fill(t.jobs.retryStarted, { file: jobName(job) })),
					onError: (err) => toast.error(messageFor(toApiError(err)).title),
				});
			}}
		>
			{t.jobs.retry}
		</Button>
	);
}

/**
 * GET /ingest/jobs for this knowledge base with a status filter and paging; polls every 3 s while
 * any row is still moving. A row opens a drawer with its errors and similar documents.
 */
export function JobsTable({
	kb,
	status,
	page,
	onFilter,
}: {
	kb: string;
	status?: JobStatus;
	page: number;
	onFilter: (next: { status?: JobStatus; page?: number }) => void;
}) {
	const filterId = useId();
	const q = useQuery(jobsQuery({ corpus_id: kb, status, offset: (page - 1) * JOBS_PAGE_SIZE, limit: JOBS_PAGE_SIZE }));
	const [openId, setOpenId] = useState<string | null>(null);
	const rows = q.data?.items ?? [];
	const total = q.data?.total ?? 0;
	const open = rows.find((j) => j.id === openId) ?? null;

	const columns: Column<Job>[] = [
		{
			id: "file",
			header: t.jobs.colFile,
			cell: (j) => (
				<Button
					variant="link"
					className="h-auto max-w-[360px] justify-start truncate p-0 font-semibold text-foreground"
					aria-label={`${t.jobs.open}: ${jobName(j)}`}
					onClick={() => setOpenId(j.id)}
				>
					{jobName(j)}
				</Button>
			),
		},
		{
			id: "mode",
			header: t.jobs.colMode,
			cell: (j) => (j.mode ? ((t.jobs.modes as Record<string, string>)[j.mode] ?? j.mode) : "—"),
			stackLabel: true,
		},
		{ id: "status", header: t.jobs.colStatus, cell: (j) => <JobStatusBadge status={j.status} /> },
		{
			id: "progress",
			header: t.jobs.colProgress,
			align: "right",
			cell: (j) =>
				`${formatNumber(j.processed_documents)} / ${j.total_documents == null ? "—" : formatNumber(j.total_documents)}`,
			stackLabel: true,
		},
		{
			id: "created",
			header: t.jobs.colCreated,
			cell: (j) => <span title={formatDateTime(j.created_at)}>{formatRelative(j.created_at)}</span>,
			stackLabel: true,
		},
		{
			id: "finished",
			header: t.jobs.colFinished,
			cell: (j) =>
				j.finished_at ? <span title={formatDateTime(j.finished_at)}>{formatRelative(j.finished_at)}</span> : "—",
			stackLabel: true,
		},
		{
			id: "actions",
			header: t.jobs.colActions,
			align: "right",
			cell: (j) => (canRetry(j) ? <RetryButton job={j} /> : null),
		},
	];

	return (
		<div className="grid gap-3">
			<div className="flex flex-wrap items-center gap-2.5">
				<label htmlFor={filterId} className="text-[13px] font-semibold">
					{t.jobs.filter}
				</label>
				<NativeSelect
					id={filterId}
					className="w-56 max-w-full"
					value={status ?? ""}
					onChange={(e) =>
						onFilter({ status: (e.target.value || undefined) as JobStatus | undefined, page: undefined })
					}
				>
					<option value="">{t.jobs.allStatuses}</option>
					{JOB_STATUSES.map((s) => (
						<option key={s} value={s}>
							{stageLabel(s)}
						</option>
					))}
				</NativeSelect>
			</div>
			{q.isPending ? (
				<div className="rounded-lg border border-border bg-card">
					<TableSkeleton rows={4} />
				</div>
			) : q.isError ? (
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			) : (
				<div className="overflow-x-auto rounded-lg border border-border bg-card">
					<DataTable
						caption={t.jobs.caption}
						columns={columns}
						rows={rows}
						rowKey={(j) => j.id}
						empty={status ? t.jobs.emptyFiltered : t.jobs.empty}
						onRowClick={(j) => setOpenId(j.id)}
					/>
					{total > JOBS_PAGE_SIZE ? (
						<Pager page={page} pageSize={JOBS_PAGE_SIZE} total={total} onPage={(p) => onFilter({ status, page: p })} />
					) : null}
				</div>
			)}
			<Sheet open={!!open} onOpenChange={(o) => !o && setOpenId(null)}>
				<SheetContent side="right" className="w-[440px] max-w-[92vw] gap-0 overflow-y-auto p-0">
					{open ? <JobDetails job={open} kb={kb} /> : null}
				</SheetContent>
			</Sheet>
		</div>
	);
}

function JobDetails({ job, kb }: { job: Job; kb: string }) {
	const errors = Object.entries(job.errors ?? {});
	const records = Object.entries(job.records ?? {});
	return (
		<>
			<SheetHeader className="border-b border-border p-4">
				<SheetTitle className="pr-8 break-words">{fill(t.jobs.detailsFor, { file: jobName(job) })}</SheetTitle>
				<SheetDescription asChild>
					<div>
						<JobStatusBadge status={job.status} />
					</div>
				</SheetDescription>
			</SheetHeader>
			<div className="grid gap-4 p-4">
				{canRetry(job) ? (
					<div>
						<RetryButton job={job} />
					</div>
				) : null}
				<KeyValue
					rows={[
						{ label: t.jobs.id, value: job.id },
						{ label: t.jobs.colMode, value: job.mode ?? "—" },
						{ label: t.jobs.colCreated, value: formatDateTime(job.created_at) },
						{ label: t.jobs.colFinished, value: formatDateTime(job.finished_at) },
						{
							label: t.jobs.colProgress,
							value: `${formatNumber(job.processed_documents)} / ${job.total_documents == null ? "—" : formatNumber(job.total_documents)}`,
						},
					]}
				/>
				{records.length ? (
					<section className="grid gap-2">
						<h3 className="text-[14px] font-bold">{t.jobs.recordCounts}</h3>
						<KeyValue rows={records.map(([k, v]) => ({ label: k, value: formatNumber(v) }))} />
					</section>
				) : null}
				<section className="grid gap-2">
					<h3 className="text-[14px] font-bold">{t.jobs.errors}</h3>
					{errors.length ? (
						<KeyValue
							rows={errors.map(([k, v]) => ({ label: k, value: typeof v === "string" ? v : JSON.stringify(v) }))}
						/>
					) : (
						<p className="m-0 text-[13px] text-muted-foreground">{t.jobs.noErrors}</p>
					)}
				</section>
				{job.similar_documents?.length ? (
					<section className="grid gap-2">
						<h3 className="text-[14px] font-bold">{t.jobs.similar}</h3>
						{job.status === "pending_review" ? (
							<PendingReviewCard job={job} kb={kb} compact />
						) : (
							<ul className="m-0 grid gap-1.5 pl-[18px] text-[13px]">
								{job.similar_documents.map((s) => (
									<li key={s.document_id}>
										{s.file_name ?? s.document_id}{" "}
										{fill(t.review.matchDetail, {
											similarity: formatPercent(s.similarity),
											matches: formatNumber(s.match_count),
										})}
									</li>
								))}
							</ul>
						)}
					</section>
				) : null}
			</div>
		</>
	);
}
