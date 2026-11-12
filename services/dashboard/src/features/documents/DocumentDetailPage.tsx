import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { ExternalLink, GitMerge, Lock, MoreHorizontal, SearchX, Trash2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { NotAvailable } from "@/components/shell/NotAvailable";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import type { Corpus } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { formatBytes } from "@/lib/format/bytes";
import { formatDateTime } from "@/lib/format/date";
import { can, LOCKED_TEXT, type OwnerAction } from "@/lib/permissions";
import { DOCUMENT_TABS, type DocumentTab } from "@/lib/search";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { CHUNK_PAGE_SIZE, type DocumentDetail, useDeleteDocument, useDocument, useRestoreVersion } from "./api";
import { ChunkList } from "./ChunkList";
import { MergeDialog } from "./MergeDialog";
import { ReviewInPortalButton } from "./ReviewInPortalButton";
import { isSystemDocument, mimeLabel, safeHttpUrl } from "./ui";
import { numberVersions, VersionTable, versionLabel } from "./VersionTable";

const route = getRouteApi("/_app/documents/$documentId");

/** `/documents/$documentId?kb=&tab=&version=&chunkPage=`. */
export function DocumentDetailPage() {
	return <KbScopedPage title={t.detail.crumbs}>{(kb) => <DocumentView key={kb.id} kb={kb} />}</KbScopedPage>;
}

function DocumentView({ kb }: { kb: Corpus }) {
	const { documentId } = route.useParams();
	const { tab = "passages", version, chunkPage = 1 } = route.useSearch();
	const navigate = useNavigate();
	const q = useDocument(kb.id, documentId, { version, chunkOffset: (chunkPage - 1) * CHUNK_PAGE_SIZE });

	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={2} />
				<CardSkeleton lines={6} />
			</div>
		);
	}
	if (q.isError) {
		if (isApiError(q.error) && q.error.status === 404 && !version) return <DocumentNotFound kb={kb.id} />;
		if (isApiError(q.error) && q.error.status === 403) return <NotAvailable />;
		return (
			<>
				<PageHeader title={t.detail.crumbs} crumbs={[{ label: t.detail.crumbs }]} />
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			</>
		);
	}
	const doc = q.data;
	const setSearch = (next: { tab?: DocumentTab; version?: string; chunkPage?: number }) =>
		void navigate({ to: ".", search: (prev) => ({ ...prev, kb: kb.id, ...next }) });

	return (
		<Loaded
			kb={kb}
			doc={doc}
			tab={tab}
			chunkPage={chunkPage}
			onTab={(v) => setSearch({ tab: v })}
			onVersion={(v) => setSearch({ tab: "passages", version: v, chunkPage: undefined })}
			onChunkPage={(p) => setSearch({ chunkPage: p })}
		/>
	);
}

function Loaded({
	kb,
	doc,
	tab,
	chunkPage,
	onTab,
	onVersion,
	onChunkPage,
}: {
	kb: Corpus;
	doc: DocumentDetail;
	tab: DocumentTab;
	chunkPage: number;
	onTab: (t: DocumentTab) => void;
	onVersion: (id: string | undefined) => void;
	onChunkPage: (p: number) => void;
}) {
	const navigate = useNavigate();
	const [merging, setMerging] = useState(false);
	const [deleting, setDeleting] = useState(false);
	const [restoringId, setRestoringId] = useState<string | null>(null);
	const del = useDeleteDocument(kb.id);
	const restore = useRestoreVersion(kb.id, doc.id);
	const name = doc.file_name || doc.external_id || doc.id;
	const versions = numberVersions(doc.versions);
	const shown = versions.find((v) => v.id === doc.version_id) ?? null;
	const viewingOther = !!doc.version_id && doc.version_id !== doc.active_version_id;
	const source = safeHttpUrl(doc.source_uri);
	const system = isSystemDocument(doc);

	const onDelete = () =>
		del.mutate(
			{ id: doc.id, confirmName: name },
			{
				onSuccess: () => {
					toast.success(fill(t.detail.deleted, { file: name }));
					setDeleting(false);
					void navigate({ to: "/documents", search: { kb: kb.id } });
				},
				onError: (e) => {
					const err = toApiError(e);
					if (err.status === 404) {
						toast(fill(t.detail.alreadyGone, { file: name }));
						setDeleting(false);
						void navigate({ to: "/documents", search: { kb: kb.id } });
						return;
					}
					const m = messageFor(err);
					toast.error(m.title, m.body ? { description: m.body } : undefined);
				},
			},
		);

	const onRestore = (versionId: string, n: number) => {
		setRestoringId(versionId);
		restore.mutate(versionId, {
			onSuccess: () => toast.success(fill(t.versions.restored, { n })),
			onError: (e) => {
				const err = toApiError(e);
				const m =
					err.status >= 500 ? { title: t.versions.rebuildFailed, body: messageFor(err).title } : messageFor(err);
				toast.error(m.title, {
					description: m.body,
					action:
						err.code === "document_limit_reached"
							? { label: t.common.seePlan, onClick: () => void navigate({ to: "/plan" }) }
							: undefined,
				});
			},
			onSettled: () => setRestoringId(null),
		});
	};

	const rows: { label: string; value: ReactNode }[] = [
		{ label: t.detail.type, value: mimeLabel(doc.mime_type) },
		{ label: t.detail.size, value: formatBytes(doc.size_bytes) },
		{ label: t.detail.added, value: formatDateTime(doc.ingested_at) },
	];
	if (doc.external_id) rows.push({ label: t.detail.externalId, value: doc.external_id });
	if (doc.source_uri) {
		rows.push({
			label: t.detail.source,
			value: source ? (
				<a
					href={source}
					target="_blank"
					rel="noopener noreferrer"
					className="inline-flex items-center gap-1 text-primary"
				>
					{doc.source_uri}
					<ExternalLink className="size-3.5" aria-hidden />
				</a>
			) : (
				doc.source_uri
			),
		});
	}
	const meta = Object.entries(doc.metadata ?? {});

	return (
		<>
			<PageHeader
				title={name}
				crumbs={[{ label: t.detail.crumbs, to: "/documents", search: { kb: kb.id } }, { label: name }]}
				description={
					<span className="inline-flex flex-wrap items-center gap-2">
						<span>{kb.name}</span>
						{doc.active_version_id ? (
							<Badge variant="ok">{t.documents.live}</Badge>
						) : (
							<Badge variant="warn">{t.documents.noActive}</Badge>
						)}
					</span>
				}
				actions={
					<>
						{system ? null : <ReviewInPortalButton kb={kb.id} versionId={doc.active_version_id} name={name} />}
						{system ? null : (
							<ActionsMenu
								items={[
									{ action: "document.merge", label: t.detail.merge, icon: GitMerge, onSelect: () => setMerging(true) },
									{
										action: "document.delete",
										label: t.detail.delete,
										icon: Trash2,
										onSelect: () => setDeleting(true),
										destructive: true,
									},
								]}
							/>
						)}
					</>
				}
			/>
			{system ? (
				<FormAlert tone="info">
					<span>{t.system.note} </span>
					<Link
						to="/kbs/$kbId"
						params={{ kbId: kb.id }}
						search={{ tab: "assistant", section: "identity" }}
						className="font-semibold underline"
					>
						{t.system.edit}
					</Link>
				</FormAlert>
			) : null}
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
				<Panel title={t.detail.info}>
					<KeyValue rows={rows} />
				</Panel>
				<Panel title={t.detail.metadata}>
					{meta.length ? (
						<KeyValue
							rows={meta.map(([k, v]) => ({ label: k, value: typeof v === "string" ? v : JSON.stringify(v) }))}
						/>
					) : (
						<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.detail.noMetadata}</p>
					)}
				</Panel>
			</div>
			<Tabs value={tab} onValueChange={(v) => onTab(v as DocumentTab)} className="min-w-0 gap-4">
				<TabsList
					variant="line"
					aria-label={t.detail.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0"
				>
					{DOCUMENT_TABS.map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className={cn(
								"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
								"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
							)}
						>
							{t.detail.tabs[k]}
							{k === "versions" ? (
								<span className="font-mono text-[12px] text-muted-foreground">{versions.length}</span>
							) : null}
						</TabsTrigger>
					))}
				</TabsList>
				<TabsContent value="passages" className="min-w-0">
					{tab === "passages" ? (
						<section className="rounded-lg border border-border bg-card" aria-label={t.detail.tabs.passages}>
							<div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 text-[13px] text-foreground-2">
								<span>
									{fill(t.passages.showing, {
										version:
											viewingOther && shown
												? versionLabel(shown.number)
												: shown
													? `${versionLabel(shown.number)} (${t.passages.activeVersion})`
													: t.passages.activeVersion,
									})}
								</span>
								{viewingOther ? (
									<Button size="sm" variant="outline" onClick={() => onVersion(undefined)}>
										{t.passages.backToActive}
									</Button>
								) : null}
							</div>
							<ChunkList
								chunks={doc.chunks}
								total={doc.chunk_total}
								page={chunkPage}
								onPage={onChunkPage}
								versionLabel={shown ? versionLabel(shown.number) : t.passages.activeVersion}
							/>
						</section>
					) : null}
				</TabsContent>
				<TabsContent value="versions" className="min-w-0">
					{tab === "versions" ? (
						<div className="overflow-x-auto rounded-lg border border-border bg-card">
							<VersionTable
								versions={versions}
								viewing={doc.version_id ?? null}
								restoringId={restoringId}
								onView={(v) => onVersion(v.id === doc.active_version_id ? undefined : v.id)}
								onRestore={(v) => onRestore(v.id, v.number)}
							/>
						</div>
					) : null}
				</TabsContent>
			</Tabs>
			<MergeDialog open={merging} onOpenChange={setMerging} kb={kb.id} documentId={doc.id} fileName={name} />
			<ConfirmDialog
				open={deleting}
				onOpenChange={setDeleting}
				title={fill(t.detail.deleteTitle, { file: name })}
				body={t.detail.deleteBody}
				confirmLabel={t.detail.deleteConfirm}
				pending={del.isPending}
				onConfirm={onDelete}
			/>
		</>
	);
}

interface MenuItemSpec {
	action: OwnerAction;
	label: string;
	icon: typeof Trash2;
	onSelect: () => void;
	destructive?: boolean;
}

/**
 * Owner-only actions in a menu. For a member session each item stays visible, is
 * aria-disabled with "Only owners can …" under it, and choosing it shows that text as a toast.
 * "Review in Content Portal" (D11) sits beside this menu when the portal is on.
 */
function ActionsMenu({ items }: { items: MenuItemSpec[] }) {
	const principal = usePrincipal();
	return (
		// Not modal: a modal menu hides the page (aria-hidden) and, when an item opens a dialog, the
		// two can race and leave the page hidden. The menu still closes on Escape and outside clicks.
		<DropdownMenu modal={false}>
			<DropdownMenuTrigger asChild>
				<Button variant="outline" aria-label={t.detail.actions}>
					<MoreHorizontal aria-hidden />
					<span className="max-[520px]:sr-only">{t.detail.actions}</span>
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" className="w-[280px]">
				{items.map((it) => {
					const allowed = can(principal, it.action);
					const Icon = allowed ? it.icon : Lock;
					return (
						<DropdownMenuItem
							key={it.label}
							aria-disabled={allowed ? undefined : "true"}
							data-locked={allowed ? undefined : ""}
							className={cn(
								"items-start",
								!allowed && "opacity-60",
								allowed && it.destructive && "text-destructive focus:text-destructive",
							)}
							onSelect={() => {
								if (allowed) it.onSelect();
								else toast(LOCKED_TEXT[it.action]);
							}}
						>
							<Icon className="mt-0.5" aria-hidden />
							<span className="grid">
								<span>{it.label}</span>
								{allowed ? null : <small className="text-muted-foreground">{LOCKED_TEXT[it.action]}</small>}
							</span>
						</DropdownMenuItem>
					);
				})}
			</DropdownMenuContent>
		</DropdownMenu>
	);
}

function DocumentNotFound({ kb }: { kb: string }) {
	useDocumentTitle(t.detail.notFoundTitle);
	return (
		<section
			data-testid="document-not-found"
			className="grid justify-items-start gap-3.5 rounded-lg border border-border bg-card p-8"
		>
			<span className="grid size-[52px] place-items-center rounded-[14px] bg-secondary text-muted-foreground">
				<SearchX className="size-[26px]" aria-hidden />
			</span>
			<h1 className="text-2xl font-bold" tabIndex={-1}>
				{t.detail.notFoundTitle}
			</h1>
			<p className="m-0 max-w-[560px] text-foreground-2">{t.detail.notFoundBody}</p>
			<Link to="/documents" search={{ kb }} className={buttonVariants({ variant: "outline" })}>
				{t.detail.back}
			</Link>
		</section>
	);
}
