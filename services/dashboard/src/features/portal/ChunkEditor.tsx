import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
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
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { formatNumber } from "@/lib/format/number";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import {
	actorFields,
	CHUNKS_PAGE_SIZE,
	type Chunk,
	type ChunkStatus,
	chunksQuery,
	portal,
	useWorkspaceMutation,
	type Workspace,
} from "./api";
import { ChunkCard } from "./ChunkCard";
import type { LockState } from "./lock";
import { MergeBar } from "./MergeBar";
import { toastPortalError } from "./messages";
import { ReorderList } from "./ReorderList";
import { SplitDialog } from "./SplitDialog";

const c = t.chunks;
type Filter = "all" | ChunkStatus;

/** In a post-ingest review, the copies of parent chunks (their text is rebuilt from children). */
export function parentCopyIds(chunks: Chunk[]): Set<string> {
	const parents = new Set<string>();
	for (const ch of chunks) {
		const p = ch.metadata_?.parent_chunk_id;
		if (typeof p === "string" && p) parents.add(p);
	}
	const out = new Set<string>();
	for (const ch of chunks) {
		const own = ch.metadata_?.engine_chunk_id;
		if (typeof own === "string" && parents.has(own)) out.add(ch.id);
	}
	return out;
}

/** The chunk editor (D11): needs the lock for every change; all ids come from this workspace's list. */
export function ChunkEditor({
	w,
	lock,
	status,
	page,
	onFilter,
	onPage,
}: {
	w: Workspace;
	lock: LockState;
	status: ChunkStatus | undefined;
	page: number;
	onFilter: (s: ChunkStatus | undefined) => void;
	onPage: (p: number) => void;
}) {
	const principal = usePrincipal();
	const actor = actorFields(principal);
	const q = useQuery(chunksQuery(w.id, { status, page }));
	const [selected, setSelected] = useState<string[]>([]);
	const [splitting, setSplitting] = useState<Chunk | null>(null);
	const [rejecting, setRejecting] = useState<Chunk | null>(null);
	const [deleting, setDeleting] = useState<Chunk | null>(null);
	const [adding, setAdding] = useState(false);

	const items = useMemo(() => [...(q.data?.items ?? [])].sort((a, b) => a.chunk_index - b.chunk_index), [q.data]);
	const parents = useMemo(() => parentCopyIds(items), [items]);
	const numberOf = (ch: Chunk) => ch.chunk_index + 1;
	const canEdit = lock.mine;
	const total = q.data?.total ?? 0;
	const reorderable = canEdit && !status && total <= CHUNKS_PAGE_SIZE;

	// Selection only holds chunks still listed (a merge or delete removes some).
	useEffect(() => {
		setSelected((s) => s.filter((id) => items.some((x) => x.id === id)));
	}, [items]);

	const onError = (e: unknown) => {
		const err = toApiError(e);
		if (err.status === 409 && !err.code && /\block/i.test(err.detail)) lock.lost();
		toastPortalError(e, "edit");
	};
	const run = useWorkspaceMutation(w.id, (fn: () => Promise<unknown>) => fn());
	const act = (fn: () => Promise<unknown>, done: string, after?: () => void) =>
		run.mutate(fn, {
			onSuccess: () => {
				toast.success(done);
				after?.();
			},
			onError,
		});

	const filters: { value: Filter; label: string }[] = [
		{ value: "all", label: c.filters.all },
		{ value: "draft", label: c.filters.draft },
		{ value: "approved", label: c.filters.approved },
		{ value: "rejected", label: c.filters.rejected },
	];

	return (
		<section aria-label={c.title} className="grid gap-3">
			<div className="flex flex-wrap items-center gap-3">
				<Segmented<Filter>
					label={c.filterLabel}
					value={status ?? "all"}
					options={filters}
					onChange={(v) => onFilter(v === "all" ? undefined : v)}
				/>
				<span className="text-[13px] text-foreground-2" data-testid="chunk-counts">
					{fill(c.counts, {
						total: formatNumber(w.chunk_count ?? total),
						approved: formatNumber(w.approved_chunk_count ?? 0),
					})}
				</span>
				<span className="ml-auto">
					{canEdit ? (
						<Button variant="default" size="sm" onClick={() => setAdding(true)}>
							<Plus aria-hidden />
							{c.add}
						</Button>
					) : null}
				</span>
			</div>
			{canEdit && !reorderable && total > 1 ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">{c.reorderOff}</p>
			) : null}
			{canEdit ? (
				<MergeBar
					count={selected.length}
					pending={run.isPending}
					onClear={() => setSelected([])}
					onMerge={(separator) => {
						const ids = items.filter((x) => selected.includes(x.id)).map((x) => x.id);
						act(
							() => portal.mergeChunks(w.id, ids, separator, actor),
							fill(t.merge.done, { n: ids.length }),
							() => setSelected([]),
						);
					}}
				/>
			) : null}
			{q.isPending ? (
				<div className="grid gap-3" aria-busy="true">
					<CardSkeleton lines={3} />
					<CardSkeleton lines={3} />
				</div>
			) : q.isError ? (
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			) : items.length === 0 ? (
				<p className="m-0 rounded-lg border border-border bg-card p-4 text-foreground-2" data-testid="chunks-empty">
					{status ? c.emptyFiltered : c.empty}
				</p>
			) : (
				<ReorderList
					items={items}
					enabled={reorderable && !run.isPending}
					label={c.listLabel}
					onReorder={(ids, to) => act(() => portal.reorderChunks(w.id, ids, actor), fill(c.moved, { n: to }))}
					render={(ch, _i, api) => {
						const n = numberOf(ch);
						return (
							<ChunkCard
								chunk={ch}
								n={n}
								canEdit={canEdit}
								parentDerived={parents.has(ch.id)}
								selected={selected.includes(ch.id)}
								busy={run.isPending}
								onSelect={(on) => setSelected((s) => (on ? [...new Set([...s, ch.id])] : s.filter((x) => x !== ch.id)))}
								onSave={(text) => act(() => portal.editChunk(w.id, ch.id, text, actor), fill(c.saved, { n }))}
								onApprove={() => act(() => portal.approveChunk(w.id, ch.id, actor), fill(c.approved, { n }))}
								onReject={() => setRejecting(ch)}
								onSplit={() => setSplitting(ch)}
								onDelete={() => setDeleting(ch)}
								onMoveUp={reorderable ? api.up : undefined}
								onMoveDown={reorderable ? api.down : undefined}
								onDragStart={reorderable ? api.dragStart : undefined}
							/>
						);
					}}
				/>
			)}
			{total > CHUNKS_PAGE_SIZE ? (
				<div className="rounded-lg border border-border bg-card">
					<Pager page={page} pageSize={CHUNKS_PAGE_SIZE} total={total} onPage={onPage} />
				</div>
			) : null}

			<SplitDialog
				chunk={splitting}
				n={splitting ? numberOf(splitting) : 0}
				pending={run.isPending}
				onOpenChange={(o) => {
					if (!o) setSplitting(null);
				}}
				onSplit={(position) => {
					if (!splitting) return;
					const ch = splitting;
					act(
						() => portal.splitChunk(w.id, ch.id, position, actor),
						fill(t.split.done, { n: numberOf(ch) }),
						() => setSplitting(null),
					);
				}}
			/>
			<RejectDialog
				chunk={rejecting}
				n={rejecting ? numberOf(rejecting) : 0}
				pending={run.isPending}
				onOpenChange={(o) => {
					if (!o) setRejecting(null);
				}}
				onReject={(reason) => {
					if (!rejecting) return;
					const ch = rejecting;
					act(
						() => portal.rejectChunk(w.id, ch.id, reason, actor),
						fill(c.rejected, { n: numberOf(ch) }),
						() => setRejecting(null),
					);
				}}
			/>
			<ConfirmDialog
				open={!!deleting}
				onOpenChange={(o) => {
					if (!o) setDeleting(null);
				}}
				title={fill(c.deleteTitle, { n: deleting ? numberOf(deleting) : 0 })}
				body={c.deleteBody}
				confirmLabel={c.delete}
				pending={run.isPending}
				onConfirm={() => {
					if (!deleting) return;
					const ch = deleting;
					act(
						() => portal.deleteChunk(w.id, ch.id, actor),
						fill(c.deleted, { n: numberOf(ch) }),
						() => setDeleting(null),
					);
				}}
			/>
			<AddChunkDialog
				open={adding}
				onOpenChange={setAdding}
				chunks={items}
				pending={run.isPending}
				onAdd={(body) =>
					act(
						() => portal.addChunk(w.id, body, actor),
						t.addChunk.added,
						() => setAdding(false),
					)
				}
			/>
		</section>
	);
}

function RejectDialog({
	chunk,
	n,
	pending,
	onOpenChange,
	onReject,
}: {
	chunk: Chunk | null;
	n: number;
	pending: boolean;
	onOpenChange: (open: boolean) => void;
	onReject: (reason: string | null) => void;
}) {
	const [reason, setReason] = useState("");
	const id = useId();
	useEffect(() => {
		if (chunk) setReason("");
	}, [chunk]);
	return (
		<ConfirmDialog
			open={!!chunk}
			onOpenChange={onOpenChange}
			title={fill(c.rejectTitle, { n })}
			body={chunk ? <span className="line-clamp-3">{chunk.text}</span> : ""}
			confirmLabel={c.rejectSubmit}
			pending={pending}
			onConfirm={() => onReject(reason.trim() || null)}
		>
			<Field id={id} label={c.rejectReason}>
				<Textarea id={id} rows={3} maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} />
			</Field>
		</ConfirmDialog>
	);
}

const addChunkSchema = z.object({
	text: z.string().trim().min(1, t.addChunk.textRequired),
	position: z.string(),
	doc: z.string(),
});
type AddChunkValues = z.infer<typeof addChunkSchema>;

function AddChunkDialog({
	open,
	onOpenChange,
	chunks,
	pending,
	onAdd,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	chunks: Chunk[];
	pending: boolean;
	onAdd: (body: { text: string; chunk_index: number | null; staged_document_id: string | null }) => void;
}) {
	const a = t.addChunk;
	const ids = { text: useId(), pos: useId(), doc: useId() };
	const form = useAppForm<AddChunkValues>({
		resolver: zodResolver(addChunkSchema),
		defaultValues: { text: "", position: "", doc: "" },
	});
	const error = form.formState.errors.text?.message;
	const submit = useAppSubmit(form, (v) =>
		onAdd({
			text: v.text.trim(),
			chunk_index: v.position === "" ? null : Number(v.position),
			staged_document_id: v.doc || null,
		}),
	);
	useEffect(() => {
		if (open) form.reset({ text: "", position: "", doc: "" });
	}, [open, form]);
	// One option per staged document of this workspace, named by its first chunk.
	const docs = useMemo(() => {
		const seen = new Map<string, number>();
		for (const ch of chunks) {
			if (ch.staged_document_id && !seen.has(ch.staged_document_id))
				seen.set(ch.staged_document_id, ch.chunk_index + 1);
		}
		return [...seen.entries()];
	}, [chunks]);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
				<form noValidate className="grid gap-4" onSubmit={(e) => void submit.onSubmit(e)}>
					<DialogHeader>
						<DialogTitle>{a.title}</DialogTitle>
						<DialogDescription className="sr-only">{a.title}</DialogDescription>
					</DialogHeader>
					<Field id={ids.text} label={a.text} error={error}>
						<Textarea
							id={ids.text}
							rows={6}
							maxLength={50000}
							aria-invalid={!!error || undefined}
							aria-describedby={describedBy(ids.text, { error: !!error })}
							{...form.register("text")}
						/>
					</Field>
					<Field id={ids.pos} label={a.position}>
						<NativeSelect id={ids.pos} {...form.register("position")}>
							<option value="">{a.positionEnd}</option>
							{chunks.map((ch) => (
								<option key={ch.id} value={String(ch.chunk_index)}>
									{fill(a.positionBefore, { n: ch.chunk_index + 1 })}
								</option>
							))}
						</NativeSelect>
					</Field>
					{docs.length ? (
						<Field id={ids.doc} label={a.document} hint={a.documentHint}>
							<NativeSelect
								id={ids.doc}
								aria-describedby={describedBy(ids.doc, { hint: true })}
								{...form.register("doc")}
							>
								<option value="">{a.documentNone}</option>
								{docs.map(([id, n]) => (
									<option key={id} value={id}>
										{fill(a.documentOf, { n })}
									</option>
								))}
							</NativeSelect>
						</Field>
					) : null}
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={pending}>
							{a.submit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
