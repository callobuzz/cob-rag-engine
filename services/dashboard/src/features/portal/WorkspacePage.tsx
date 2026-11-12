import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Lock, LockOpen, SearchX, Trash2, UserPen } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { NotAvailable } from "@/components/shell/NotAvailable";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { TabsContent } from "@/components/ui/tabs";
import { isApiError, toApiError } from "@/lib/api/errors";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { AiChatPanel } from "./AiChatPanel";
import { AuditTrail } from "./AuditTrail";
import { chunksQuery, type FinalizeResult, pk, portal, useWorkspace, type Workspace } from "./api";
import { ChunkEditor } from "./ChunkEditor";
import { type LockState, useWorkspaceLock } from "./lock";
import { portalMessage } from "./messages";
import { PostIngestSummary } from "./PostIngestSummary";
import { PostIngestBar, ReviewBar } from "./ReviewBar";
import { StagingPanel } from "./StagingPanel";
import { KindBadge, LineTabs, type Panel, PortalGate, type WorkspaceSearch, WorkspaceStatusBadge } from "./ui";

const route = getRouteApi("/_app/portal/$workspaceId");
const w_ = t.workspace;

/** `/portal/$workspaceId?kb=&panel=&status=&page=` (D11). */
export function WorkspacePage() {
	return (
		<PortalGate>
			<WorkspaceLoader />
		</PortalGate>
	);
}

function WorkspaceLoader() {
	const { workspaceId } = route.useParams();
	const q = useWorkspace(workspaceId);
	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={2} />
				<CardSkeleton lines={6} />
			</div>
		);
	}
	if (q.isError) {
		if (isApiError(q.error) && (q.error.status === 404 || q.error.status === 422)) return <WorkspaceNotFound />;
		if (isApiError(q.error) && q.error.status === 403) return <NotAvailable />;
		return (
			<>
				<PageHeader title={w_.crumbs} />
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			</>
		);
	}
	return <WorkspaceView key={q.data.id} w={q.data} />;
}

function WorkspaceView({ w }: { w: Workspace }) {
	const search = route.useSearch();
	const navigate = useNavigate();
	const qc = useQueryClient();
	const lock = useWorkspaceLock(w);
	const kbs = useQuery(corporaSwitcherQuery);
	const kbName = kbs.data?.items.find((k) => k.id === w.corpus_id)?.name ?? "";
	const review = w.kind === "post_ingest_review";
	const panels: Panel[] = review ? ["chunks", "chat", "audit"] : ["staging", "chunks", "chat", "audit"];
	const panel: Panel = search.panel && panels.includes(search.panel) ? search.panel : review ? "chunks" : "staging";
	const [auditPage, setAuditPage] = useState(1);
	const [deleting, setDeleting] = useState(false);
	const [deleteError, setDeleteError] = useState<string | null>(null);
	const [pendingDelete, setPendingDelete] = useState(false);
	const [finalized, setFinalized] = useState<FinalizeResult | null>(null);
	// Chunk numbers for the audit trail (first page).
	const chunks = useQuery({ ...chunksQuery(w.id, {}), enabled: panel === "audit" });
	const numbers = useMemo(
		() => new Map((chunks.data?.items ?? []).map((c) => [c.id, c.chunk_index + 1])),
		[chunks.data],
	);

	const setSearch = (next: Partial<WorkspaceSearch>) =>
		void navigate({ to: ".", search: (prev) => ({ ...prev, kb: w.corpus_id, ...next }), replace: true });

	const onDelete = async () => {
		setDeleteError(null);
		setPendingDelete(true);
		try {
			await portal.remove(w.id);
			qc.removeQueries({ queryKey: pk.workspace(w.id) });
			await qc.invalidateQueries({ queryKey: pk.lists });
			toast.success(fill(w_.deleted, { title: w.title }));
			setDeleting(false);
			void navigate({ to: "/portal", search: { kb: w.corpus_id } });
		} catch (e) {
			setDeleteError(portalMessage(toApiError(e)).title);
		} finally {
			setPendingDelete(false);
		}
	};

	return (
		<div className="grid gap-5">
			<PageHeader
				title={w.title}
				crumbs={[{ label: w_.crumbs, to: "/portal", search: { kb: w.corpus_id } }, { label: w.title }]}
				description={
					<span className="inline-flex flex-wrap items-center gap-2">
						<KindBadge kind={w.kind ?? "standard"} />
						<WorkspaceStatusBadge status={w.status} />
						{kbName ? <span>{kbName}</span> : null}
						<span className="text-[12.5px] text-muted-foreground">{fill(w_.createdBy, { who: w.created_by })}</span>
						{w.assigned_reviewer ? (
							<span className="text-[12.5px] text-muted-foreground">
								{fill(w_.reviewer, { who: w.assigned_reviewer })}
							</span>
						) : null}
					</span>
				}
				actions={
					<OwnerGate action="portal.deleteWorkspace">
						<Button
							variant="destructive"
							onClick={() => {
								setDeleteError(null);
								setDeleting(true);
							}}
						>
							<Trash2 aria-hidden />
							{w_.delete}
						</Button>
					</OwnerGate>
				}
			/>

			{w.review_feedback && w.status === "draft" ? (
				<FormAlert tone="warn">
					<b className="font-semibold">{w_.feedback}: </b>
					{w.review_feedback}
				</FormAlert>
			) : null}

			<LockBar lock={lock} status={w.status} />

			{review ? <PostIngestBar w={w} lock={lock} onFinalized={setFinalized} /> : <ReviewBar w={w} kb={w.corpus_id} />}
			{finalized ? <PostIngestSummary result={finalized} kb={w.corpus_id} /> : null}

			<LineTabs
				label={w_.panelsLabel}
				value={panel}
				options={panels.map((p) => ({ value: p, label: w_.panels[p] }))}
				onChange={(p) => setSearch({ panel: p, status: undefined, page: undefined })}
			>
				<TabsContent value="staging" className="min-w-0">
					{panel === "staging" ? <StagingPanel w={w} canAdd={!lock.closed} /> : null}
				</TabsContent>
				<TabsContent value="chunks" className="min-w-0">
					{panel === "chunks" ? (
						<ChunkEditor
							w={w}
							lock={lock}
							status={search.status}
							page={search.page ?? 1}
							onFilter={(s) => setSearch({ status: s, page: undefined })}
							onPage={(p) => setSearch({ page: p > 1 ? p : undefined })}
						/>
					) : null}
				</TabsContent>
				<TabsContent value="chat" className="min-w-0">
					{panel === "chat" ? <AiChatPanel w={w} canApply={lock.mine} /> : null}
				</TabsContent>
				<TabsContent value="audit" className="min-w-0">
					{panel === "audit" ? (
						<AuditTrail
							workspaceId={w.id}
							page={auditPage}
							onPage={setAuditPage}
							numberOf={(id) => numbers.get(id) ?? null}
						/>
					) : null}
				</TabsContent>
			</LineTabs>

			<TypedConfirmDialog
				open={deleting}
				onOpenChange={setDeleting}
				title={fill(w_.deleteTitle, { title: w.title })}
				body={w_.deleteBody}
				word={w.title}
				confirmLabel={w_.deleteConfirm}
				pending={pendingDelete}
				error={deleteError}
				onConfirm={() => void onDelete()}
			/>
		</div>
	);
}

/** The lock state, announced politely: mine (Stop editing), someone else's (read-only), free (Start editing). */
function LockBar({ lock, status }: { lock: LockState; status: string }) {
	const l = t.lock;
	let text: string;
	let Icon = LockOpen;
	if (lock.closed) text = fill(l.closed, { status: (t.status[status] ?? status).toLowerCase() });
	else if (lock.mine) {
		text = l.mine;
		Icon = UserPen;
	} else if (lock.holder) {
		Icon = Lock;
		text = lock.lockedAt
			? fill(l.other, {
					holder: lock.holder,
					time: new Date(lock.lockedAt).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" }),
				})
			: fill(l.otherNoTime, { holder: lock.holder });
	} else text = l.free;
	return (
		<section
			aria-label={l.region}
			data-testid="lock-bar"
			data-state={lock.closed ? "closed" : lock.mine ? "mine" : lock.holder ? "other" : "free"}
			className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-card px-4 py-3"
		>
			<Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
			<p role="status" className="m-0 min-w-0 flex-1 basis-[220px] text-[13px] text-foreground-2">
				{text}
				{lock.holder && lock.lockedAt ? <span className="sr-only"> ({formatDateTime(lock.lockedAt)})</span> : null}
			</p>
			{lock.closed ? null : lock.mine ? (
				<Button size="sm" variant="outline" disabled={lock.pending} onClick={lock.stop}>
					{l.stop}
				</Button>
			) : (
				<Button size="sm" variant="default" disabled={lock.pending} onClick={lock.start}>
					{lock.pending ? l.starting : l.start}
				</Button>
			)}
		</section>
	);
}

function WorkspaceNotFound() {
	useDocumentTitle(w_.notFoundTitle);
	const { kb } = route.useSearch();
	return (
		<section
			data-testid="workspace-not-found"
			className="grid justify-items-start gap-3.5 rounded-lg border border-border bg-card p-8"
		>
			<span className="grid size-[52px] place-items-center rounded-[14px] bg-secondary text-muted-foreground">
				<SearchX className="size-[26px]" aria-hidden />
			</span>
			<h1 className="text-2xl font-bold" tabIndex={-1}>
				{w_.notFoundTitle}
			</h1>
			<p className="m-0 max-w-[560px] text-foreground-2">{w_.notFoundBody}</p>
			<Link to="/portal" search={{ kb }} className={buttonVariants({ variant: "outline" })}>
				{w_.back}
			</Link>
		</section>
	);
}
