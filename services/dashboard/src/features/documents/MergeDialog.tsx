import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { messageFor } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { contentStrings as t } from "@/lib/strings/content";
import { en, fill } from "@/lib/strings/en";
import { useMergeDocument } from "./api";
import { DocumentPicker, type PickedDocument } from "./DocumentPicker";

/**
 * "Merge into another document…" (owner, A5): POST /documents/{this}/replace/{other} moves this
 * document's versions under the other one and removes this record; then open the other one.
 */
export function MergeDialog({
	open,
	onOpenChange,
	kb,
	documentId,
	fileName,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	kb: string;
	documentId: string;
	fileName: string;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-xl">
				{open ? (
					<MergeForm kb={kb} documentId={documentId} fileName={fileName} onClose={() => onOpenChange(false)} />
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function MergeForm({
	kb,
	documentId,
	fileName,
	onClose,
}: {
	kb: string;
	documentId: string;
	fileName: string;
	onClose: () => void;
}) {
	const [target, setTarget] = useState<PickedDocument | null>(null);
	const [error, setError] = useState<ApiError | null>(null);
	const [missing, setMissing] = useState(false);
	const merge = useMergeDocument(kb, documentId);
	const navigate = useNavigate();
	const errId = useId();

	const submit = () => {
		if (!target) {
			setMissing(true);
			return;
		}
		setError(null);
		merge.mutate(target.id, {
			onSuccess: () => {
				toast.success(fill(t.merge.done, { target: target.name }));
				onClose();
				void navigate({
					to: "/documents/$documentId",
					params: { documentId: target.id },
					search: { kb, tab: "versions" },
				});
			},
			onError: (e) => setError(toApiError(e)),
		});
	};
	const msg = error ? messageFor(error) : null;

	return (
		<>
			<DialogHeader>
				<DialogTitle>{fill(t.merge.title, { file: fileName })}</DialogTitle>
				<DialogDescription>{fill(t.merge.lead, { file: fileName })}</DialogDescription>
			</DialogHeader>
			<DocumentPicker
				kb={kb}
				label={t.merge.find}
				value={target}
				excludeId={documentId}
				invalid={missing && !target}
				describedBy={missing && !target ? errId : undefined}
				onChange={(v) => {
					setTarget(v);
					setMissing(false);
				}}
			/>
			{missing && !target ? (
				<p id={errId} role="alert" className="m-0 text-[13px] text-destructive">
					{t.merge.pick}
				</p>
			) : null}
			{msg ? (
				<div role="alert" className="grid gap-1 rounded-md bg-destructive-soft p-2.5 text-[13px] text-destructive">
					<b className="font-semibold">{msg.title}</b>
					{msg.body ? <span>{msg.body}</span> : null}
				</div>
			) : null}
			<DialogFooter>
				<Button variant="outline" onClick={onClose}>
					{en.common.cancel}
				</Button>
				<Button variant="default" disabled={merge.isPending} onClick={submit}>
					{merge.isPending ? t.merge.merging : t.merge.submit}
				</Button>
			</DialogFooter>
		</>
	);
}
