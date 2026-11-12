import { Link, useNavigate } from "@tanstack/react-router";
import { CopyPlus, FileWarning, Replace, SkipForward } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { formatPercent } from "@/lib/format/number";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { type Job, useConfirmReview } from "./api";

/**
 * A "looks like an existing document" decision (mockup near-duplicate card): add as new, skip it, or
 * replace one of the similar documents. 409 not pending → "Already handled"; 410 → expired;
 * 409 document_limit_reached → the plan message.
 */
export function PendingReviewCard({
	job,
	kb,
	onConfirmed,
	compact = false,
}: {
	job: Job;
	kb: string;
	onConfirmed?: () => void;
	compact?: boolean;
}) {
	const similar = job.similar_documents ?? [];
	const [target, setTarget] = useState(similar[0]?.document_id ?? "");
	const [error, setError] = useState<ApiError | null>(null);
	const [skipping, setSkipping] = useState(false);
	const confirm = useConfirmReview(kb);
	const navigate = useNavigate();
	const selectId = useId();
	const titleId = useId();
	const file = job.file_name ?? t.review.unknownFile;
	const chosen = similar.find((s) => s.document_id === target) ?? similar[0];

	const decide = (action: "new" | "replace" | "skip") => {
		setError(null);
		confirm.mutate(
			{ jobId: job.id, action, target: action === "replace" ? chosen?.document_id : undefined },
			{
				onSuccess: () => {
					setSkipping(false);
					toast.success(
						action === "new"
							? fill(t.review.confirmedNew, { file })
							: action === "skip"
								? fill(t.review.confirmedSkip, { file })
								: fill(t.review.confirmedReplace, { file, target: chosen?.file_name ?? t.review.unknownFile }),
						compact
							? undefined
							: {
									action: {
										label: t.review.seeJobs,
										onClick: () => void navigate({ to: "/add", search: { kb, tab: "jobs" } }),
									},
								},
					);
					onConfirmed?.();
				},
				onError: (e) => {
					setSkipping(false);
					setError(toApiError(e));
				},
			},
		);
	};

	const errorText = error ? reviewError(error) : null;
	const handled = error?.status === 409 && error.code !== "document_limit_reached";
	const expired = error?.status === 410;

	return (
		<article
			aria-labelledby={titleId}
			data-testid="pending-review"
			className="grid gap-3 rounded-lg border border-[color-mix(in_srgb,var(--warning)_45%,var(--border))] bg-card p-4"
		>
			<div className="flex items-start gap-3">
				<span className="grid size-9 shrink-0 place-items-center rounded-md bg-warning-soft text-warning">
					<FileWarning className="size-[18px]" aria-hidden />
				</span>
				<div className="grid min-w-0 gap-1">
					<h3 id={titleId} className="text-[14px] font-bold">
						{t.review.title}
					</h3>
					{chosen ? (
						<p className="m-0 text-[13px] text-foreground-2">
							{fill(t.review.body, {
								file,
								target: chosen.file_name ?? t.review.unknownFile,
								similarity: formatPercent(chosen.similarity),
							})}
						</p>
					) : null}
					{job.expires_at ? (
						<p className="m-0 text-[12.5px] text-muted-foreground">
							{fill(t.review.expires, { when: formatDateTime(job.expires_at) })}
						</p>
					) : null}
				</div>
			</div>
			{similar.length > 1 ? (
				<div className="grid gap-1.5">
					<label htmlFor={selectId} className="text-[13px] font-semibold">
						{t.review.replaceWhich}
					</label>
					<NativeSelect id={selectId} value={target} onChange={(e) => setTarget(e.target.value)}>
						{similar.map((s) => (
							<option key={s.document_id} value={s.document_id}>
								{`${s.file_name ?? t.review.unknownFile} · ${formatPercent(s.similarity)}`}
							</option>
						))}
					</NativeSelect>
				</div>
			) : null}
			{errorText ? (
				<div role="alert" className="grid gap-1 rounded-md bg-destructive-soft p-2.5 text-[13px] text-destructive">
					<b className="font-semibold">{errorText.title}</b>
					{errorText.body ? <span>{errorText.body}</span> : null}
					{error?.code === "document_limit_reached" ? (
						<Link to="/plan" className="font-semibold text-destructive underline">
							{t.common.seePlan}
						</Link>
					) : null}
				</div>
			) : null}
			{handled || expired ? null : (
				<div className="flex flex-wrap gap-2">
					<Button size="sm" variant="outline" disabled={confirm.isPending} onClick={() => decide("new")}>
						<CopyPlus aria-hidden />
						{t.review.addNew}
					</Button>
					{chosen ? (
						<Button size="sm" variant="default" disabled={confirm.isPending} onClick={() => decide("replace")}>
							<Replace aria-hidden />
							{fill(t.review.replace, { file: chosen.file_name ?? t.review.unknownFile })}
						</Button>
					) : null}
					<Button size="sm" variant="ghost" disabled={confirm.isPending} onClick={() => setSkipping(true)}>
						<SkipForward aria-hidden />
						{t.review.skip}
					</Button>
				</div>
			)}
			<ConfirmDialog
				open={skipping}
				onOpenChange={setSkipping}
				title={fill(t.review.skipTitle, { file })}
				body={t.review.skipBody}
				confirmLabel={t.review.skipConfirm}
				destructive={false}
				pending={confirm.isPending}
				onConfirm={() => decide("skip")}
			/>
		</article>
	);
}

export function reviewError(e: ApiError): { title: string; body?: string } {
	if (e.status === 410) return { title: t.review.expired };
	if (e.status === 409 && e.code !== "document_limit_reached")
		return { title: t.review.handled, body: t.review.handledBody };
	return messageFor(e);
}
