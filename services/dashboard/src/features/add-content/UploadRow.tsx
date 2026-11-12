import { Link } from "@tanstack/react-router";
import { ChevronDown, CircleAlert, Info, RotateCcw, X } from "lucide-react";
import { useId, useState } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { DocumentPicker } from "@/features/documents/DocumentPicker";
import { PendingReviewCard } from "@/features/documents/PendingReviewCard";
import { MimeIcon } from "@/features/documents/ui";
import { messageFor } from "@/lib/api/error-messages";
import type { ApiError } from "@/lib/api/errors";
import { useRagUrl } from "@/lib/auth/store";
import { formatBytes } from "@/lib/format/bytes";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { useJob } from "./api";
import { JobStages, stageLabel } from "./JobStages";
import { INGEST_MODES, type IngestMode, optionProblems, type UploadItem, uploadQueue } from "./upload-queue";

/**
 * One file: options while it waits (mode, target, metadata, skip the duplicate check), byte
 * progress while it uploads, then the job's live stages; a needs-review job shows the review
 * card, failures show their reason with Retry (same idempotency key).
 */
export function UploadRow({ item, announce }: { item: UploadItem; announce: (m: string) => void }) {
	const name = item.file.name;
	return (
		<li
			data-testid="upload-row"
			data-phase={item.phase}
			className="grid gap-3 border-b border-border px-4 py-3.5 last:border-b-0"
			aria-label={name}
		>
			<div className="flex min-w-0 items-center gap-3">
				<MimeIcon mime={item.file.type} />
				<div className="min-w-0 flex-1">
					<b className="block truncate text-[13.5px]" title={name}>
						{name}
					</b>
					<span className="text-[12px] text-muted-foreground">{formatBytes(item.file.size)}</span>
				</div>
				{item.phase === "ready" || item.phase === "waiting" || item.phase === "rejected" || item.phase === "error" ? (
					<Button
						size="icon-sm"
						variant="ghost"
						aria-label={fill(t.upload.remove, { file: name })}
						onClick={() => uploadQueue.remove(item.id)}
					>
						<X aria-hidden />
					</Button>
				) : null}
			</div>
			<RowBody item={item} announce={announce} />
		</li>
	);
}

function RowBody({ item, announce }: { item: UploadItem; announce: (m: string) => void }) {
	switch (item.phase) {
		case "rejected":
			return (
				<p className="m-0 flex items-center gap-1.5 text-[13px] text-destructive">
					<CircleAlert className="size-4 shrink-0" aria-hidden />
					<span>
						<b className="font-semibold">{t.upload.rejected}:</b> {item.reason}
					</span>
				</p>
			);
		case "ready":
		case "waiting":
			return (
				<>
					{item.phase === "waiting" ? (
						<p className="m-0 text-[12.5px] font-semibold text-muted-foreground">{t.stages.waiting}</p>
					) : null}
					<RowOptions item={item} />
				</>
			);
		case "uploading":
			return <ByteProgress item={item} />;
		case "error":
			return <UploadError item={item} error={item.error} />;
		case "sent":
			return item.jobId ? <SentJob item={item} jobId={item.jobId} announce={announce} /> : null;
	}
}

function ByteProgress({ item }: { item: UploadItem }) {
	const pct = Math.round(item.progress * 100);
	return (
		<div className="grid gap-1.5">
			<div
				role="progressbar"
				aria-label={fill(t.upload.progress, { file: item.file.name })}
				aria-valuemin={0}
				aria-valuemax={100}
				aria-valuenow={pct}
				aria-valuetext={`${t.stages.sending}: ${fill(t.upload.sentBytes, { pct })}`}
				className="h-1.5 overflow-hidden rounded-full bg-secondary"
			>
				<div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
			</div>
			<p className="m-0 text-[12.5px] font-semibold text-foreground-2">
				{t.stages.sending}{" "}
				<span className="font-normal text-muted-foreground">· {fill(t.upload.sentBytes, { pct })}</span>
			</p>
		</div>
	);
}

function SentJob({ item, jobId, announce }: { item: UploadItem; jobId: string; announce: (m: string) => void }) {
	const q = useJob(item.kb, jobId, (status) => {
		uploadQueue.setJobStatus(item.id, status);
		announce(fill(t.upload.announceStage, { file: item.file.name, stage: stageLabel(status) }));
	});
	const job = q.data;
	if (q.isError) {
		const gone = q.error && (q.error as ApiError).status === 404;
		return (
			<div role="alert" className="flex flex-wrap items-center gap-2 text-[13px] text-destructive">
				<CircleAlert className="size-4" aria-hidden />
				{gone ? t.upload.jobGone : messageFor(q.error as ApiError).title}
				{gone ? null : (
					<Button size="sm" variant="outline" onClick={() => void q.refetch()}>
						{t.upload.retry}
					</Button>
				)}
			</div>
		);
	}
	const status = job?.status ?? item.jobStatus ?? "queued";
	const failedAt = typeof job?.errors?.failed_at_step === "string" ? job.errors.failed_at_step : null;
	const reason = typeof job?.errors?.error === "string" ? job.errors.error : null;
	return (
		<div className="grid gap-2.5">
			{item.alreadyReceived ? (
				<p className="m-0 flex items-center gap-1.5 text-[12.5px] text-foreground-2">
					<Info className="size-3.5" aria-hidden />
					<b className="font-semibold">{t.upload.alreadyReceived}</b> · {t.upload.alreadyReceivedBody}
				</p>
			) : null}
			<JobStages status={status} failedAt={failedAt} name={item.file.name} />
			{status === "failed" ? (
				<div role="alert" className="grid gap-2 rounded-md bg-destructive-soft p-2.5 text-[13px] text-destructive">
					<span>
						{reason ?? (failedAt ? fill(t.upload.failedAt, { stage: stageLabel(failedAt) }) : t.stages.failed)}
					</span>
					<Button
						size="sm"
						variant="outline"
						className="justify-self-start"
						onClick={() => {
							uploadQueue.add(item.kb, [item.file], [null]);
						}}
					>
						<RotateCcw aria-hidden />
						{t.upload.uploadAgain}
					</Button>
				</div>
			) : null}
			{status === "pending_review" && job ? <PendingReviewCard job={job} kb={item.kb} compact /> : null}
		</div>
	);
}

function UploadError({ item, error }: { item: UploadItem; error: ApiError | null }) {
	const ragUrl = useRagUrl();
	if (!error) return null;
	const msg = uploadErrorMessage(error, ragUrl);
	return (
		<div role="alert" className="grid gap-2 rounded-md bg-destructive-soft p-2.5 text-[13px] text-destructive">
			<b className="font-semibold">{msg.title}</b>
			{msg.body ? <span>{msg.body}</span> : null}
			<div className="flex flex-wrap gap-2">
				{error.code === "document_limit_reached" ? (
					<Link to="/plan" className="font-semibold text-destructive underline">
						{t.common.seePlan}
					</Link>
				) : null}
				<Button size="sm" variant="outline" onClick={() => uploadQueue.retry(item.id)}>
					<RotateCcw aria-hidden />
					{t.upload.retry}
				</Button>
			</div>
		</div>
	);
}

/** HTTP errors of POST /ingest in plain words (D4 error table). */
export function uploadErrorMessage(e: ApiError, ragUrl?: string): { title: string; body?: string } {
	if (e.code === "document_limit_reached" || e.code === "owner_required" || e.code === "network") {
		return messageFor(e, { ragUrl });
	}
	if (e.status === 409 && /in use by an upload in progress/i.test(e.detail))
		return { title: t.upload.inProgressElsewhere };
	if (e.status === 409 && /idempotency_key/i.test(e.detail)) return { title: t.upload.alreadyReceived, body: e.detail };
	if (e.status === 503 && (/storage/i.test(e.detail) || !e.detail)) return { title: t.upload.storageDown };
	if ((e.status === 400 || e.status === 413 || e.status === 422 || e.status === 503) && e.detail)
		return { title: e.detail };
	return messageFor(e, { ragUrl });
}

function RowOptions({ item }: { item: UploadItem }) {
	const ids = { mode: useId(), meta: useId(), metaHint: useId(), skip: useId(), target: useId() };
	const [advanced, setAdvanced] = useState(item.options.metadata !== "" || item.options.autoConfirm);
	const o = item.options;
	const problems = optionProblems(o);
	const needsTarget = o.mode === "replace" || o.mode === "version";
	const set = (patch: Partial<typeof o>) => uploadQueue.setOptions(item.id, patch);
	return (
		<fieldset className="grid min-w-0 gap-3 border-0 p-0">
			<legend className="sr-only">{fill(t.upload.options, { file: item.file.name })}</legend>
			<div className="grid gap-1.5 min-[861px]:max-w-[420px]">
				<label htmlFor={ids.mode} className="text-[13px] font-semibold">
					{t.upload.mode}
				</label>
				<NativeSelect
					id={ids.mode}
					value={o.mode}
					onChange={(e) => set({ mode: e.target.value as IngestMode, target: null })}
				>
					{INGEST_MODES.map((m) => (
						<option key={m} value={m}>
							{t.upload.modes[m]}
						</option>
					))}
				</NativeSelect>
			</div>
			{needsTarget ? (
				<div className="grid gap-1.5 min-[861px]:max-w-[520px]">
					<DocumentPicker
						kb={item.kb}
						label={t.upload.target}
						value={o.target}
						invalid={!!problems.target}
						describedBy={problems.target ? ids.target : undefined}
						onChange={(v) => set({ target: v })}
					/>
					{problems.target ? (
						<p id={ids.target} className="m-0 text-[12.5px] text-destructive">
							{t.upload.targetRequired}
						</p>
					) : null}
				</div>
			) : null}
			<div>
				<Button
					size="sm"
					variant="ghost"
					aria-expanded={advanced}
					onClick={() => setAdvanced((a) => !a)}
					className="px-1.5"
				>
					<ChevronDown className={cn("transition-transform", advanced && "rotate-180")} aria-hidden />
					{t.upload.advanced}
				</Button>
			</div>
			{advanced ? (
				<div className="grid gap-3 min-[861px]:max-w-[520px]">
					<div className="grid gap-1.5">
						<label htmlFor={ids.meta} className="text-[13px] font-semibold">
							{t.upload.metadata}
						</label>
						<Textarea
							id={ids.meta}
							rows={2}
							className="font-mono text-[12.5px]"
							value={o.metadata}
							aria-invalid={problems.metadata || undefined}
							aria-describedby={ids.metaHint}
							onChange={(e) => set({ metadata: e.target.value })}
						/>
						<p
							id={ids.metaHint}
							className={cn("m-0 text-[12.5px]", problems.metadata ? "text-destructive" : "text-muted-foreground")}
						>
							{problems.metadata ? t.upload.metadataInvalid : t.upload.metadataHint}
						</p>
					</div>
					<div className="flex items-start gap-2.5">
						<Checkbox
							id={ids.skip}
							checked={o.autoConfirm}
							onCheckedChange={(v) => set({ autoConfirm: v === true })}
							className="mt-0.5"
						/>
						<label htmlFor={ids.skip} className="grid text-[13px]">
							<span className="font-semibold">{t.upload.skipCheck}</span>
							<span className="text-muted-foreground">{t.upload.skipCheckHint}</span>
						</label>
					</div>
				</div>
			) : null}
		</fieldset>
	);
}
