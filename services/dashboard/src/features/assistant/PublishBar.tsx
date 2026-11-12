import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import { KbErrorAlert } from "../kbs/KbErrorAlert";
import { useDiscardDraft, usePublish } from "./api";
import { useAssistantDirty } from "./dirty";
import type { AssistantState } from "./types";

/**
 * Status, Discard, Preview and the owner-only Publish. Publishing sends the saved draft at the
 * revision shown here; a newer one is a conflict ("Load their version"), never an overwrite.
 */
export function PublishBar({ kb, state, onPreview }: { kb: string; state: AssistantState; onPreview: () => void }) {
	const qc = useQueryClient();
	const noteId = useId();
	const publish = usePublish(kb);
	const discard = useDiscardDraft(kb);
	const { dirty, setPublishError } = useAssistantDirty();
	const [open, setOpen] = useState(false);
	const [confirmDiscard, setConfirmDiscard] = useState(false);
	const [note, setNote] = useState("");
	const [error, setError] = useState<ApiError | null>(null);
	const live = state.published;
	const status = !live
		? t.publish.notLive
		: state.enabled
			? fill(t.publish.live, { version: live.version ?? 0 })
			: fill(t.publish.off, { version: live.version ?? 0 });
	const second = state.draft
		? [t.publish.draftPending, fill(t.publish.draftBy, { who: state.draft.created_by })].join(" · ")
		: t.publish.noDraft;

	const run = async () => {
		setError(null);
		try {
			const out = await publish.mutateAsync({
				note: note.trim() || undefined,
				base_revision: state.draft?.draft_revision ?? 0,
			});
			setOpen(false);
			setNote("");
			setPublishError(null);
			toast.success(fill(t.publish.publishedToast, { version: out.version ?? 0 }));
		} catch (e) {
			const err = toApiError(e);
			setError(err);
			setPublishError(err);
		}
	};

	return (
		<div
			className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3"
			data-testid="publish-bar"
		>
			<div className="grid gap-0.5">
				<span className="text-[14px] font-semibold">{status}</span>
				<span className="text-[12.5px] text-muted-foreground">{second}</span>
				{state.draft && dirty ? <span className="text-[12.5px] text-warning">{t.publish.unsaved}</span> : null}
			</div>
			<div className="flex flex-wrap gap-2">
				<Button variant="ghost" disabled={!state.draft} onClick={() => setConfirmDiscard(true)}>
					{t.publish.discard}
				</Button>
				<Button variant="outline" onClick={onPreview}>
					{t.publish.preview}
				</Button>
				<OwnerGate action="assistant.publish">
					<Button
						disabled={!state.draft || publish.isPending || dirty}
						onClick={() => {
							setError(null);
							setOpen(true);
						}}
					>
						{t.publish.publish}
					</Button>
				</OwnerGate>
			</div>
			<Dialog open={open} onOpenChange={setOpen}>
				<DialogContent>
					<DialogHeader>
						<DialogTitle>{t.publish.dialogTitle}</DialogTitle>
					</DialogHeader>
					{error ? <KbErrorAlert error={error} /> : null}
					{error?.code === "assistant_draft_conflict" ? (
						<Button
							variant="outline"
							size="sm"
							className="justify-self-start"
							onClick={() => {
								setOpen(false);
								setError(null);
								void qc.invalidateQueries({ queryKey: qk.assistant.state(kb) });
							}}
						>
							{t.publish.loadTheirs}
						</Button>
					) : null}
					<div className="grid gap-1">
						<label htmlFor={noteId} className="text-[13px] font-semibold">
							{t.publish.note}
						</label>
						<Textarea id={noteId} maxLength={200} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
					</div>
					<DialogFooter>
						<Button variant="ghost" onClick={() => setOpen(false)}>
							{t.publish.cancel}
						</Button>
						<Button disabled={publish.isPending} onClick={() => void run()}>
							{t.publish.confirm}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
			<ConfirmDialog
				open={confirmDiscard}
				onOpenChange={setConfirmDiscard}
				title={t.publish.discardTitle}
				body={t.publish.discardBody}
				confirmLabel={t.publish.discard}
				pending={discard.isPending}
				onConfirm={() =>
					void discard.mutateAsync().then(() => {
						setConfirmDiscard(false);
						toast.success(t.publish.discardedToast);
					})
				}
			/>
		</div>
	);
}
