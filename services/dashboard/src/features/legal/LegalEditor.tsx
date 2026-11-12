import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ExternalLink, TriangleAlert } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { currentRagUrl } from "@/lib/auth/store";
import { formatDate } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { missingName, legalStrings as t } from "@/lib/strings/legal";
import { enginePageUrl, type LegalPage, legalPageQuery, usePreview, usePublish, useSaveDraft } from "./api";

const PREVIEW_DELAY_MS = 500;

/** The preview page: the engine's own safe HTML in a sandboxed frame (no scripts, no same-origin). */
function previewDoc(html: string): string {
	return `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{font:15px/1.6 system-ui,sans-serif;margin:16px;color:#1b1f24}h1{font-size:1.5rem}h2{font-size:1.15rem;margin-top:1.6rem}table{border-collapse:collapse}td,th{border:1px solid #ccd;padding:4px 8px}</style>${html}`;
}

function Preview({ slug, markdown }: { slug: string; markdown: string }) {
	const preview = usePreview(slug);
	const run = preview.mutate;
	// biome-ignore lint/correctness/useExhaustiveDependencies: `run` is stable; the text drives it
	useEffect(() => {
		const timer = setTimeout(() => run(markdown), PREVIEW_DELAY_MS);
		return () => clearTimeout(timer);
	}, [markdown]);
	return (
		<Panel title={t.editor.previewTitle}>
			<div className="grid gap-3 p-4">
				{preview.isError ? <FormAlert>{messageFor(toApiError(preview.error)).title}</FormAlert> : null}
				{preview.data ? (
					<iframe
						title={t.editor.previewFrame}
						data-testid="legal-preview"
						sandbox=""
						srcDoc={previewDoc(preview.data.html)}
						className="h-[520px] w-full rounded-lg border border-border bg-white"
					/>
				) : (
					<CardSkeleton lines={6} />
				)}
			</div>
		</Panel>
	);
}

function Missing({ names, onFix }: { names: string[]; onFix: () => void }) {
	if (names.length === 0) return null;
	return (
		<div
			role="status"
			data-testid="legal-missing"
			className="flex items-start gap-2 rounded-sm bg-warning-soft p-3 text-[13px] text-warning"
		>
			<TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
			<div className="grid gap-1">
				<strong>{t.editor.missingTitle}</strong>
				<span>{names.map(missingName).join(", ")}.</span>
				<button type="button" className="w-fit text-left font-semibold underline" onClick={onFix}>
					{t.editor.missingFix}
				</button>
			</div>
		</div>
	);
}

function Editor({ page, onBack, onFixDetails }: { page: LegalPage; onBack: () => void; onFixDetails: () => void }) {
	const id = useId();
	const save = useSaveDraft(page.slug);
	const publish = usePublish(page.slug);
	const [title, setTitle] = useState(page.title);
	const [markdown, setMarkdown] = useState(page.markdown);
	const [error, setError] = useState<string | null>(null);
	const [conflict, setConflict] = useState(false);
	const [confirming, setConfirming] = useState(false);
	const [note, setNote] = useState("");
	const [reviewed, setReviewed] = useState(false);
	const [publishError, setPublishError] = useState<string | null>(null);
	const loaded = useRef({ revision: page.revision, title: page.title, markdown: page.markdown });

	// A save (or a reload) brought new server values: start from them.
	useEffect(() => {
		if (page.revision === loaded.current.revision && page.markdown === loaded.current.markdown) return;
		loaded.current = { revision: page.revision, title: page.title, markdown: page.markdown };
		setTitle(page.title);
		setMarkdown(page.markdown);
	}, [page.revision, page.title, page.markdown]);

	const dirty = title !== page.title || markdown !== page.markdown;

	const saveDraft = async (): Promise<boolean> => {
		setError(null);
		try {
			await save.mutateAsync({ title: title.trim(), markdown, base_revision: page.revision });
			toast.success(t.editor.saved);
			return true;
		} catch (e) {
			const err = toApiError(e);
			if (err.code === "legal_draft_conflict") setConflict(true);
			setError(messageFor(err).title);
			return false;
		}
	};

	const doPublish = async () => {
		if (!reviewed) return setPublishError(t.publish.reviewedRequired);
		setPublishError(null);
		if (dirty && !(await saveDraft())) return setConfirming(false);
		try {
			// the draft just saved has a new revision; the publish takes whatever the draft is now
			const made = await publish.mutateAsync({ change_note: note.trim() || null, confirm_reviewed: true });
			toast.success(fill(t.publish.done, { version: String(made.published?.version ?? "") }));
			setConfirming(false);
			setNote("");
			setReviewed(false);
		} catch (e) {
			setPublishError(messageFor(toApiError(e)).title);
		}
	};

	return (
		<div className="grid gap-4" data-testid="legal-editor">
			<div>
				<Button variant="ghost" size="sm" onClick={onBack}>
					<ArrowLeft aria-hidden />
					{t.editor.back}
				</Button>
			</div>
			<div
				role="note"
				data-testid="legal-review-banner"
				className="flex items-start gap-2 rounded-sm bg-warning-soft p-3 text-[13px] text-warning"
			>
				<TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
				<span>{t.editor.banner}</span>
			</div>
			<Missing names={page.missing} onFix={onFixDetails} />
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-2">
				<Panel title={page.title}>
					<div className="grid gap-4 p-4">
						{error ? <FormAlert>{error}</FormAlert> : null}
						{conflict ? (
							<div>
								<Button variant="outline" size="sm" onClick={() => window.location.reload()}>
									{t.editor.reload}
								</Button>
							</div>
						) : null}
						<Field id={`${id}-title`} label={t.editor.titleLabel}>
							<Input id={`${id}-title`} maxLength={120} value={title} onChange={(e) => setTitle(e.target.value)} />
						</Field>
						<Field id={`${id}-md`} label={t.editor.markdownLabel} hint={t.editor.markdownHint}>
							<Textarea
								id={`${id}-md`}
								rows={22}
								className="font-mono text-[13px]"
								aria-describedby={`${id}-md-hint`}
								value={markdown}
								onChange={(e) => setMarkdown(e.target.value)}
							/>
						</Field>
					</div>
					<FormFoot>
						{dirty ? (
							<span className="mr-auto self-center text-[12.5px] text-muted-foreground">{t.editor.dirty}</span>
						) : null}
						<Button
							variant="outline"
							data-testid="legal-save"
							disabled={!dirty || title.trim() === "" || save.isPending}
							onClick={() => void saveDraft()}
						>
							{save.isPending ? t.editor.saving : t.editor.save}
						</Button>
						<Button data-testid="legal-publish" disabled={title.trim() === ""} onClick={() => setConfirming(true)}>
							{t.editor.publish}
						</Button>
					</FormFoot>
				</Panel>
				<div className="grid gap-4">
					<Preview slug={page.slug} markdown={markdown} />
					<Panel title={t.editor.history}>
						<div className="grid gap-2 p-4 text-[13.5px]" data-testid="legal-history">
							{page.history.length === 0 ? (
								<p className="m-0 text-foreground-2">{t.editor.historyNone}</p>
							) : (
								<>
									<ul className="m-0 grid list-none gap-1.5 p-0">
										{page.history.map((h) => (
											<li key={h.version}>
												{fill(t.editor.historyRow, {
													version: String(h.version),
													date: formatDate(h.published_at),
													who: h.published_by ?? "—",
												})}
												{h.change_note ? <span className="text-muted-foreground"> — {h.change_note}</span> : null}
											</li>
										))}
									</ul>
									<a
										className="inline-flex w-fit items-center gap-1 font-semibold text-brand-text"
										href={enginePageUrl(currentRagUrl(), page.slug)}
										target="_blank"
										rel="noopener noreferrer"
									>
										{t.editor.openLive}
										<ExternalLink className="size-3.5" aria-hidden />
									</a>
								</>
							)}
						</div>
					</Panel>
				</div>
			</div>
			<ConfirmDialog
				open={confirming}
				onOpenChange={(o) => {
					if (!o) {
						setConfirming(false);
						setPublishError(null);
					}
				}}
				title={t.publish.title}
				body={t.publish.body}
				confirmLabel={t.publish.confirm}
				destructive={false}
				pending={publish.isPending || save.isPending}
				onConfirm={() => void doPublish()}
			>
				<div className="grid gap-3" data-testid="legal-publish-form">
					{publishError ? <FormAlert>{publishError}</FormAlert> : null}
					<Field id={`${id}-note`} label={t.publish.note} hint={t.publish.noteHint}>
						<Input
							id={`${id}-note`}
							maxLength={300}
							value={note}
							aria-describedby={`${id}-note-hint`}
							onChange={(e) => setNote(e.target.value)}
						/>
					</Field>
					<div className="flex items-start gap-2">
						<Checkbox id={`${id}-reviewed`} checked={reviewed} onCheckedChange={(v) => setReviewed(v === true)} />
						<label htmlFor={`${id}-reviewed`} className="text-[13.5px]">
							{t.publish.reviewed}
						</label>
					</div>
				</div>
			</ConfirmDialog>
		</div>
	);
}

/** One page's editor: loads the draft (or the shipped template), edits, previews and publishes. */
export function LegalEditor({
	slug,
	onBack,
	onFixDetails,
}: {
	slug: string;
	onBack: () => void;
	onFixDetails: () => void;
}) {
	const q = useQuery(legalPageQuery(slug));
	if (q.isPending) return <CardSkeleton lines={8} />;
	if (q.isError) return <ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />;
	// keyed on the slug: a different page starts from its own text
	return <Editor key={slug} page={q.data} onBack={onBack} onFixDetails={onFixDetails} />;
}
