import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Check, CircleCheck, Send, Undo2, Upload } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { Button, buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import {
	actorFields,
	type FinalizeResult,
	type IngestResult,
	invalidateLive,
	invalidateWorkspace,
	portal,
	reviewStatusQuery,
	useWorkspaceMutation,
	type Workspace,
} from "./api";
import type { LockState } from "./lock";
import { portalMessage, toastPortalError } from "./messages";

const r = t.review;

function Bar({ label, children }: { label: string; children: React.ReactNode }) {
	return (
		<section
			aria-label={label}
			data-testid="review-bar"
			className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-3"
		>
			{children}
		</section>
	);
}

/**
 * Review bar of a standard workspace: Submit for review → Approve (comment) / Send back (feedback
 * required) → Ingest (`POST …/ingest`), with the resulting job linked to Add content → Jobs.
 */
export function ReviewBar({ w, kb }: { w: Workspace; kb: string }) {
	const principal = usePrincipal();
	const actor = actorFields(principal);
	const navigate = useNavigate();
	const [approving, setApproving] = useState(false);
	const [rejecting, setRejecting] = useState(false);
	const [comment, setComment] = useState("");
	const [feedback, setFeedback] = useState("");
	const [feedbackError, setFeedbackError] = useState<string | null>(null);
	const [ingested, setIngested] = useState<IngestResult | null>(null);
	const ids = { comment: useId(), feedback: useId() };
	const run = useWorkspaceMutation(w.id, (fn: () => Promise<unknown>) => fn());
	const ingest = useWorkspaceMutation(w.id, () => portal.ingest(w.id, actor), { live: w.corpus_id });

	const onIngest = () =>
		ingest.mutate(undefined, {
			onSuccess: (res) => {
				setIngested(res);
				toast.success(fill(r.ingested, { n: formatNumber(res.chunks_ingested) }));
				// The lock (if any) has no use on an ingested workspace.
				if (w.locked_by) void portal.unlock(w.id, actor).catch(() => undefined);
			},
			onError: (e) => {
				const err = toApiError(e);
				const m = portalMessage(err);
				toast.error(m.title, {
					description: m.body,
					action:
						err.code === "document_limit_reached"
							? { label: r.seePlan, onClick: () => void navigate({ to: "/plan" }) }
							: undefined,
				});
			},
		});

	const note =
		w.status === "draft"
			? r.draftNote
			: w.status === "in_review"
				? r.inReviewNote
				: w.status === "approved"
					? r.approvedNote
					: w.status === "ingested"
						? r.done
						: null;

	return (
		<Bar label={r.label}>
			<div className="grid min-w-0 flex-1 basis-[240px] gap-0.5">
				<b className="text-[13.5px]">{r.steps}</b>
				{note ? <span className="text-[12.5px] text-foreground-2">{note}</span> : null}
				{ingested?.job_id ? (
					<span className="text-[12.5px]">
						<Link to="/add" search={{ kb, tab: "jobs" }} className="font-semibold text-primary">
							{r.viewJob}
						</Link>
					</span>
				) : null}
			</div>
			{w.status === "draft" ? (
				<Button
					variant="default"
					disabled={run.isPending}
					onClick={() =>
						run.mutate(() => portal.submit(w.id, actor), {
							onSuccess: () => toast.success(r.submitted),
							onError: (e) => toastPortalError(e),
						})
					}
				>
					<Send aria-hidden />
					{r.submit}
				</Button>
			) : null}
			{w.status === "in_review" ? (
				<>
					<Button
						variant="outline"
						onClick={() => {
							setFeedback("");
							setFeedbackError(null);
							setRejecting(true);
						}}
					>
						<Undo2 aria-hidden />
						{r.reject}
					</Button>
					<Button
						variant="default"
						onClick={() => {
							setComment("");
							setApproving(true);
						}}
					>
						<Check aria-hidden />
						{r.approve}
					</Button>
				</>
			) : null}
			{w.status === "approved" ? (
				<Button variant="default" disabled={ingest.isPending} onClick={onIngest}>
					<Upload aria-hidden />
					{ingest.isPending ? r.ingesting : r.ingest}
				</Button>
			) : null}
			{w.status === "ingested" ? (
				<Link to="/documents" search={{ kb }} className={buttonVariants({ variant: "outline" })}>
					<CircleCheck aria-hidden />
					{r.openDocuments}
				</Link>
			) : null}

			<ConfirmDialog
				open={approving}
				onOpenChange={setApproving}
				title={fill(r.approveTitle, { title: w.title })}
				body={r.inReviewNote}
				confirmLabel={r.approveSubmit}
				destructive={false}
				pending={run.isPending}
				onConfirm={() =>
					run.mutate(() => portal.approve(w.id, comment.trim() || null, actor), {
						onSuccess: () => {
							toast.success(r.approved);
							setApproving(false);
						},
						onError: (e) => toastPortalError(e),
					})
				}
			>
				<Field id={ids.comment} label={r.approveComment}>
					<Textarea
						id={ids.comment}
						rows={3}
						maxLength={2000}
						value={comment}
						onChange={(e) => setComment(e.target.value)}
					/>
				</Field>
			</ConfirmDialog>
			<ConfirmDialog
				open={rejecting}
				onOpenChange={setRejecting}
				title={fill(r.rejectTitle, { title: w.title })}
				body={r.inReviewNote}
				confirmLabel={r.rejectSubmit}
				pending={run.isPending}
				onConfirm={() => {
					if (!feedback.trim()) {
						setFeedbackError(r.rejectRequired);
						return;
					}
					run.mutate(() => portal.reject(w.id, feedback.trim(), actor), {
						onSuccess: () => {
							toast.success(r.rejected);
							setRejecting(false);
						},
						onError: (e) => toastPortalError(e),
					});
				}}
			>
				<Field id={ids.feedback} label={r.rejectFeedback} error={feedbackError}>
					<Textarea
						id={ids.feedback}
						rows={4}
						maxLength={5000}
						aria-required="true"
						aria-invalid={!!feedbackError}
						aria-describedby={feedbackError ? `${ids.feedback}-error` : undefined}
						value={feedback}
						onChange={(e) => setFeedback(e.target.value)}
					/>
				</Field>
			</ConfirmDialog>
		</Bar>
	);
}

/**
 * Post-ingest review: counts from `GET …/review-status` and "Publish as new version"
 * (`POST …/finalize-review`); every coded refusal is shown in the bar.
 */
export function PostIngestBar({
	w,
	lock,
	onFinalized,
}: {
	w: Workspace;
	lock: LockState;
	onFinalized: (result: FinalizeResult) => void;
}) {
	const principal = usePrincipal();
	const actor = actorFields(principal);
	const qc = useQueryClient();
	const status = useQuery(reviewStatusQuery(w.id));
	const [error, setError] = useState<string | null>(null);
	const finalize = useWorkspaceMutation(w.id, () => portal.finalize(w.id, actor), { live: w.corpus_id });
	const p = t.postIngest;
	const s = status.data;
	const finalized = w.status === "ingested" || !!w.finalized_version_id;
	const gone = !finalized && s !== undefined && s.document_version_id === null;

	return (
		<Bar label={r.label}>
			<div className="grid min-w-0 flex-1 basis-[240px] gap-0.5">
				<b className="text-[13.5px]">{finalized ? p.finalized : p.note}</b>
				{s ? (
					<span className="text-[12.5px] text-foreground-2" data-testid="review-counts">
						{fill(p.counts, {
							total: formatNumber(s.total_chunks),
							approved: formatNumber(s.approved_chunks + s.draft_chunks),
							rejected: formatNumber(s.rejected_chunks),
							modified: formatNumber(s.modified_chunks),
						})}
					</span>
				) : null}
				{gone ? <span className="text-[12.5px] text-warning">{p.reviewedGone}</span> : null}
				{error ? (
					<FormAlert className="mt-1.5" id="finalize-error">
						{error}
					</FormAlert>
				) : null}
			</div>
			{!finalized ? (
				<Button
					variant="default"
					disabled={finalize.isPending}
					onClick={() => {
						setError(null);
						finalize.mutate(undefined, {
							onSuccess: (res) => {
								toast.success(p.published);
								if (lock.mine) void portal.unlock(w.id, actor).catch(() => undefined);
								onFinalized(res);
								void invalidateLive(qc, w.corpus_id);
								void invalidateWorkspace(qc, w.id);
							},
							onError: (e) => setError(portalMessage(e, "finalize").title),
						});
					}}
				>
					<Upload aria-hidden />
					{finalize.isPending ? p.publishing : p.publish}
				</Button>
			) : null}
		</Bar>
	);
}
