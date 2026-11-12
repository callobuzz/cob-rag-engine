import { useState } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { type LandingLang, usePublish } from "./api";
import { useDraft } from "./DraftProvider";
import { describePath } from "./draft";

/**
 * Publish the draft as the next version, with an optional note. Unsaved edits are saved first.
 * When a switched-on language still lacks texts the engine refuses and names them; each is a link
 * that closes this window and opens that field in that language.
 */
export function PublishDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
	const draft = useDraft();
	const publish = usePublish();
	const [note, setNote] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [gaps, setGaps] = useState<{ paths: string[]; lang: LandingLang } | null>(null);

	const close = (next: boolean) => {
		onOpenChange(next);
		if (!next) {
			setError(null);
			setGaps(null);
		}
	};

	const run = async () => {
		setError(null);
		setGaps(null);
		let revision = draft.state.draft_revision;
		if (draft.dirty) {
			const saved = await draft.save();
			if (!saved) return close(false);
			revision = saved.draft_revision;
		}
		try {
			const made = await publish.mutateAsync({ base_revision: revision, change_note: note.trim() || null });
			toast.success(fill(t.publish.done, { version: made.published?.version ?? "" }));
			setNote("");
			close(false);
		} catch (e) {
			const err = toApiError(e);
			if (err.code === "landing_translation_missing" && Array.isArray(err.extra.paths)) {
				setGaps({ paths: err.extra.paths as string[], lang: (err.extra.language as LandingLang) ?? "hi" });
			}
			setError(messageFor(err).title);
		}
	};

	return (
		<Dialog open={open} onOpenChange={close}>
			<DialogContent className="sm:max-w-lg">
				<DialogHeader>
					<DialogTitle>{t.publish.title}</DialogTitle>
					<DialogDescription>{t.publish.lead}</DialogDescription>
				</DialogHeader>
				{draft.dirty ? <p className="m-0 text-[13px] text-muted-foreground">{t.publish.saveFirst}</p> : null}
				<div className="grid gap-1.5">
					<label htmlFor="landing-note" className="text-[13px] font-semibold">
						{t.publish.note}
					</label>
					<Textarea
						id="landing-note"
						rows={2}
						value={note}
						placeholder={t.publish.notePlaceholder}
						onChange={(e) => setNote(e.target.value)}
					/>
				</div>
				{error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{error}
					</p>
				) : null}
				{gaps ? (
					<ul className="m-0 grid max-h-48 list-none gap-1 overflow-y-auto p-0" data-testid="publish-gaps">
						{gaps.paths.map((path) => (
							<li key={path}>
								<button
									type="button"
									className="text-left text-[13px] font-medium text-brand-text underline"
									onClick={() => {
										close(false);
										draft.goTo(path, gaps.lang);
									}}
								>
									{describePath(path, draft.content)}
								</button>
							</li>
						))}
					</ul>
				) : null}
				<DialogFooter>
					<Button type="button" variant="ghost" onClick={() => close(false)}>
						{t.publish.cancel}
					</Button>
					<Button
						type="button"
						variant="brand"
						data-testid="landing-publish-confirm"
						disabled={publish.isPending || draft.saving}
						onClick={() => void run()}
					>
						{t.publish.confirm}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
