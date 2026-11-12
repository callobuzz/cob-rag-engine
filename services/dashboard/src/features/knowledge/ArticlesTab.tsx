import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { FilePlus2, FileText, HelpCircle, PenLine, Upload } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { UploadTab } from "@/features/add-content/AddContentPage";
import { uploadLimits } from "@/features/add-content/api";
import { useUploadItems } from "@/features/add-content/upload-queue";
import { useDeleteDocument } from "@/features/documents/api";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { infoQuery } from "@/lib/api/queries";
import { formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { knowledgeStrings } from "@/lib/strings/knowledge";
import { ArticleEditor } from "./ArticleEditor";
import { type ArticleKind, docKind, type KnowledgeDoc, knowledgeDocsQuery } from "./api";

const t = knowledgeStrings.articles;
const route = getRouteApi("/_app/knowledge");
/** How long, after saving or uploading, the list keeps looking for the new document. */
const WATCH_MS = 45_000;

interface Editing {
	kind: ArticleKind;
	id: string | null;
	title?: string; // TV4: a question to start a new editor with
}

/** "Articles & files": what the owner wrote and uploaded, with the editors and the upload box. */
export function ArticlesTab({ kb }: { kb: string }) {
	const { new: openNew, question } = route.useSearch(); // TV4: question
	const navigate = useNavigate();
	const info = useQuery(infoQuery);
	const uploads = useUploadItems(kb);
	const [editing, setEditing] = useState<Editing | null>(openNew ? { kind: openNew, id: null, title: question } : null);
	const [showUpload, setShowUpload] = useState(false);
	const [watchUntil, setWatchUntil] = useState(0);
	const [learning, setLearning] = useState<string[]>([]);
	const [deleting, setDeleting] = useState<KnowledgeDoc | null>(null);
	const watching = watchUntil > Date.now();
	const articles = useQuery(knowledgeDocsQuery(kb, "articles", watching));
	const files = useQuery(knowledgeDocsQuery(kb, "files", watching));
	const del = useDeleteDocument(kb);

	// `?new=article|faq` (the Home quick action) opens the editor once and is then dropped from the URL.
	useEffect(() => {
		if (openNew) void navigate({ to: "/knowledge", search: { tab: "articles" }, replace: true });
	}, [openNew, navigate]);

	// Stop looking when the watch window is over (the poll itself is a query option).
	useEffect(() => {
		if (!watching) return;
		const id = setTimeout(() => setWatchUntil(0), watchUntil - Date.now());
		return () => clearTimeout(id);
	}, [watching, watchUntil]);

	const rows = useMemo(() => {
		const all = [...(articles.data?.items ?? []), ...(files.data?.items ?? [])];
		return all.sort((a, b) => b.ingested_at.localeCompare(a.ingested_at));
	}, [articles.data, files.data]);
	const names = new Set(rows.map((r) => r.file_name ?? ""));
	const stillLearning = learning.filter((title) => !names.has(title));

	const saved = (title: string) => {
		setLearning((l) => [...l, title]);
		setWatchUntil(Date.now() + WATCH_MS);
	};
	const remove = (d: KnowledgeDoc, typed: string) => {
		const title = d.file_name || d.external_id || d.id;
		del.mutate(
			{ id: d.id, confirmName: typed },
			{
				onSuccess: () => {
					toast.success(fill(t.deleted, { title }));
					setDeleting(null);
				},
				onError: (e) => {
					const err = toApiError(e);
					if (err.status === 404) {
						setDeleting(null);
						return;
					}
					const m = messageFor(err);
					toast.error(m.title, m.body ? { description: m.body } : undefined);
				},
			},
		);
	};

	const pending = articles.isPending || files.isPending;
	const failed = articles.error ?? files.error;
	const limits = info.data ? uploadLimits(info.data) : null;

	return (
		<div className="grid gap-4">
			<Panel
				title={t.title}
				sub={t.sub}
				aside={
					<div className="flex flex-wrap gap-2">
						<Button size="sm" onClick={() => setEditing({ kind: "article", id: null })}>
							<PenLine aria-hidden />
							{t.write}
						</Button>
						<Button size="sm" variant="outline" onClick={() => setEditing({ kind: "faq", id: null })}>
							<HelpCircle aria-hidden />
							{t.faq}
						</Button>
						<Button size="sm" variant="outline" aria-expanded={showUpload} onClick={() => setShowUpload((v) => !v)}>
							<Upload aria-hidden />
							{showUpload ? t.hideUpload : t.upload}
						</Button>
					</div>
				}
			>
				{pending ? (
					<TableSkeleton rows={3} />
				) : failed ? (
					<div className="p-4">
						<ErrorState
							error={failed}
							title={t.loadError}
							onRetry={() => {
								void articles.refetch();
								void files.refetch();
							}}
						/>
					</div>
				) : rows.length === 0 && stillLearning.length === 0 ? (
					<div className="p-4">
						<EmptyState
							title={t.empty}
							body={t.emptyBody}
							primaryAction={
								<Button onClick={() => setEditing({ kind: "article", id: null })}>
									<FilePlus2 aria-hidden />
									{t.write}
								</Button>
							}
						/>
					</div>
				) : (
					<ul aria-label={t.listLabel} className="m-0 grid list-none p-0">
						{stillLearning.map((title) => (
							<li
								key={`learning-${title}`}
								data-testid="knowledge-learning"
								className="border-b border-border px-4 py-3 text-[13px] text-muted-foreground last:border-b-0"
							>
								{fill(t.learning, { title })}
							</li>
						))}
						{rows.map((d) => (
							<Row
								key={d.id}
								doc={d}
								onEdit={(kind) => setEditing({ kind, id: d.id })}
								onDelete={() => setDeleting(d)}
							/>
						))}
					</ul>
				)}
			</Panel>
			{showUpload || uploads.length > 0 ? <UploadTab kb={kb} limits={limits} /> : null}
			<ArticleEditor
				key={`${editing?.kind}-${editing?.id ?? "new"}`}
				kb={kb}
				kind={editing?.kind ?? "article"}
				id={editing?.id ?? null}
				initialTitle={editing?.title} // TV4
				open={editing !== null}
				onOpenChange={(open) => {
					if (!open) setEditing(null);
				}}
				onSaved={saved}
			/>
			<TypedConfirmDialog
				open={deleting !== null}
				onOpenChange={(open) => {
					if (!open) setDeleting(null);
				}}
				title={fill(t.deleteTitle, { title: deleting?.file_name || deleting?.external_id || "" })}
				body={t.deleteBody}
				word={deleting?.file_name || deleting?.external_id || deleting?.id || ""}
				confirmLabel={t.deleteConfirm}
				pending={del.isPending}
				onConfirm={(typed) => deleting && remove(deleting, typed)}
			/>
		</div>
	);
}

function Row({
	doc,
	onEdit,
	onDelete,
}: {
	doc: KnowledgeDoc;
	onEdit: (kind: ArticleKind) => void;
	onDelete: () => void;
}) {
	const kind = docKind(doc);
	const title = doc.file_name || doc.external_id || doc.id;
	return (
		<li
			data-testid="knowledge-row"
			className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3 last:border-b-0"
		>
			<FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden />
			<div className="min-w-0 flex-1">
				<div className="flex flex-wrap items-center gap-2 font-semibold">
					<span className="min-w-0 break-words">{title}</span>
					<Badge variant={kind === "file" ? "outline" : "info"}>{t.kinds[kind]}</Badge>
				</div>
				<div className="text-[12.5px] text-muted-foreground">
					{fill(t.updated, { when: formatRelative(doc.ingested_at) })}
				</div>
			</div>
			<div className="flex gap-1.5">
				{kind === "file" ? null : (
					<Button size="sm" variant="outline" aria-label={fill(t.editFor, { title })} onClick={() => onEdit(kind)}>
						{t.edit}
					</Button>
				)}
				<OwnerGate action="document.delete">
					<Button size="sm" variant="ghost" aria-label={fill(t.deleteFor, { title })} onClick={onDelete}>
						{t.delete}
					</Button>
				</OwnerGate>
			</div>
		</li>
	);
}
