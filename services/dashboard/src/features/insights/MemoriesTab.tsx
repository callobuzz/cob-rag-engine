import { useQuery } from "@tanstack/react-query";
import { Eraser, Plus, Search, Trash2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Panel } from "@/features/kbs/ui";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { insightsStrings } from "@/lib/strings/insights";
import {
	MEMORIES_PAGE_SIZE,
	type Memory,
	memoriesQuery,
	useBulkDeleteMemories,
	useDeleteMemory,
	usePurgeMemories,
	useSearchMemories,
} from "./api";
import type { ScopeProps } from "./InsightsPage";
import { BulkMemoryDialog, MemoryDialog, ViewMemoryDialog } from "./MemoryDialog";
import { MEMORY_TYPES, type MemoryType } from "./search";
import { FilterRow, Note, useErrorText } from "./ui";

const t = insightsStrings.memories;
const plural = (w: { one: string; many: string }, n: number, extra: Record<string, string> = {}) =>
	n === 1 ? fill(w.one, extra) : fill(w.many, { n, ...extra });

/** What a bulk delete removes, in words (spec: the typed confirmation states the filter). */
export function bulkScopeText(kbName: string, type: MemoryType | undefined, user: string | undefined): string {
	const typeWord = type ? t.types[type].toLowerCase() : "";
	if (user && type) return fill(t.bulkScopeUserType, { type: typeWord, user, kb: kbName });
	if (user) return fill(t.bulkScopeUser, { user, kb: kbName });
	if (type) return fill(t.bulkScopeType, { type: typeWord, kb: kbName });
	return fill(t.bulkScopeAll, { kb: kbName });
}

/**
 * Memories of one knowledge base: list with kind and person filters, search (needs a person),
 * add one, add many (JSON), view, delete one (plain confirm), "Delete all for this filter" and
 * "Purge deleted" (owners; typed `delete`, scope stated).
 */
export function MemoriesTab({ kb, corpusId, search, setSearch }: ScopeProps) {
	const page = search.page ?? 1;
	const q = useQuery(memoriesQuery(kb.id, { type: search.mtype, user: search.user, page }));
	const [user, setUser] = useState(search.user ?? "");
	const [term, setTerm] = useState("");
	const [searchError, setSearchError] = useState<string | null>(null);
	const find = useSearchMemories(kb.id);
	const [dialog, setDialog] = useState<null | "add" | "bulk" | "bulkDelete" | "purge">(null);
	const [viewing, setViewing] = useState<string | null>(null);
	const [deleting, setDeleting] = useState<Memory | null>(null);
	const remove = useDeleteMemory(kb.id);
	const bulkDelete = useBulkDeleteMemories(kb.id);
	const purge = usePurgeMemories(kb.id);
	const errorText = useErrorText();
	const ids = { type: useId(), user: useId(), term: useId() };
	const results = find.data && find.variables?.user_id === search.user ? find.data : null;

	const onFilter = (e: FormEvent) => {
		e.preventDefault();
		find.reset();
		setSearch({ user: user.trim() || undefined, page: undefined });
	};
	const onSearch = (e: FormEvent) => {
		e.preventDefault();
		if (!search.user) {
			setSearchError(t.searchNeedsUser);
			return;
		}
		if (!term.trim()) return;
		setSearchError(null);
		find.mutate({ user_id: search.user, query: term.trim() }, { onError: (err) => setSearchError(errorText(err)) });
	};

	const columns: Column<Memory>[] = [
		{
			id: "content",
			header: t.colContent,
			rowHeader: true,
			className: "min-w-[240px]",
			cell: (m) => <span className="line-clamp-3 whitespace-pre-wrap">{m.content}</span>,
		},
		{
			id: "user",
			header: t.colUser,
			stackLabel: true,
			cell: (m) => <span className="font-mono">{m.user_id ?? "—"}</span>,
		},
		{
			id: "type",
			header: t.colType,
			stackLabel: true,
			cell: (m) => (t.types as Record<string, string>)[m.memory_type] ?? m.memory_type,
		},
		{ id: "source", header: t.colSource, stackLabel: true, cell: (m) => m.source ?? "—" },
		{
			id: "created",
			header: t.colCreated,
			stackLabel: true,
			cell: (m) => <time title={formatDateTime(m.created_at)}>{formatRelative(m.created_at)}</time>,
		},
		{
			id: "actions",
			header: "",
			cell: (m) => (
				<div className="flex gap-1.5">
					<Button
						size="sm"
						aria-label={fill(t.viewFor, { m: m.content.slice(0, 60) })}
						onClick={() => setViewing(m.id)}
					>
						{t.view}
					</Button>
					<Button
						size="sm"
						variant="destructive"
						aria-label={fill(t.deleteFor, { m: m.content.slice(0, 60) })}
						onClick={() => setDeleting(m)}
					>
						{t.delete}
					</Button>
				</div>
			),
		},
	];

	const rows = results ? results.items : (q.data?.items ?? []);
	const filtered = !!(search.user || search.mtype);

	return (
		<>
			{corpusId ? null : <Note testId="memories-one-kb">{fill(insightsStrings.scope.oneKbNote, { kb: kb.name })}</Note>}
			<Panel
				title={`${t.title} · ${kb.name}`}
				sub={t.sub}
				aside={
					<div className="flex flex-wrap gap-2">
						<Button type="button" variant="default" onClick={() => setDialog("add")}>
							<Plus aria-hidden />
							{t.add}
						</Button>
						<Button type="button" onClick={() => setDialog("bulk")}>
							{t.bulk}
						</Button>
					</div>
				}
			>
				<FilterRow className="border-b border-border">
					<form onSubmit={onFilter} className="flex flex-wrap items-end gap-2">
						<div className="grid gap-1.5">
							<label htmlFor={ids.type} className="text-[13px] font-semibold">
								{t.type}
							</label>
							<NativeSelect
								id={ids.type}
								className="w-[160px]"
								value={search.mtype ?? ""}
								onChange={(e) => {
									find.reset();
									setSearch({ mtype: (e.target.value || undefined) as MemoryType | undefined, page: undefined });
								}}
							>
								<option value="">{t.types.all}</option>
								{MEMORY_TYPES.map((m) => (
									<option key={m} value={m}>
										{t.types[m]}
									</option>
								))}
							</NativeSelect>
						</div>
						<div className="grid gap-1.5">
							<label htmlFor={ids.user} className="text-[13px] font-semibold">
								{t.user}
							</label>
							<Input
								id={ids.user}
								className="w-[220px] max-w-full font-mono"
								placeholder={t.userPlaceholder}
								value={user}
								onChange={(e) => setUser(e.target.value)}
							/>
						</div>
						<Button type="submit">{t.apply}</Button>
					</form>
					<form onSubmit={onSearch} className="flex flex-wrap items-end gap-2">
						<div className="grid gap-1.5">
							<label htmlFor={ids.term} className="text-[13px] font-semibold">
								{t.search}
							</label>
							<Input
								id={ids.term}
								type="search"
								className="w-[240px] max-w-full"
								placeholder={t.searchPlaceholder}
								value={term}
								aria-invalid={!!searchError}
								aria-describedby={searchError ? `${ids.term}-error` : undefined}
								onChange={(e) => setTerm(e.target.value)}
							/>
						</div>
						<Button type="submit" disabled={find.isPending}>
							<Search aria-hidden />
							{t.searchButton}
						</Button>
					</form>
				</FilterRow>
				{searchError ? (
					<p id={`${ids.term}-error`} role="alert" className="m-0 px-4 pt-3 text-[12.5px] font-medium text-destructive">
						{searchError}
					</p>
				) : null}
				{results ? (
					<div className="flex flex-wrap items-center justify-between gap-2 px-4 pt-3">
						<p role="status" className="m-0 text-[13px] text-foreground-2">
							{plural(t.searchResults, results.total, { q: results.query })}
						</p>
						<Button size="sm" variant="ghost" onClick={() => find.reset()}>
							{t.clearSearch}
						</Button>
					</div>
				) : null}
				{q.isError ? (
					<div className="p-4">
						<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
					</div>
				) : q.isPending ? (
					<div className="p-4">
						<TableSkeleton rows={4} />
					</div>
				) : (
					<>
						<div className="px-2">
							<DataTable
								columns={columns}
								rows={rows}
								rowKey={(m) => m.id}
								caption={t.title}
								empty={filtered || results ? t.empty : t.emptyAll}
							/>
						</div>
						{!results && q.data.total > MEMORIES_PAGE_SIZE ? (
							<Pager
								page={page}
								pageSize={MEMORIES_PAGE_SIZE}
								total={q.data.total}
								onPage={(p) => setSearch({ page: p })}
							/>
						) : null}
					</>
				)}
				<div className="flex flex-wrap justify-end gap-2 rounded-b-lg border-t border-border bg-secondary px-4 py-3">
					<OwnerGate action="memory.bulkDelete">
						<Button type="button" variant="destructive" onClick={() => setDialog("bulkDelete")}>
							<Trash2 aria-hidden />
							{t.deleteFiltered}
						</Button>
					</OwnerGate>
					<OwnerGate action="memory.bulkDelete">
						<Button type="button" variant="destructive" onClick={() => setDialog("purge")}>
							<Eraser aria-hidden />
							{t.purge}
						</Button>
					</OwnerGate>
				</div>
			</Panel>

			<MemoryDialog
				open={dialog === "add"}
				onOpenChange={(o) => setDialog(o ? "add" : null)}
				kb={kb.id}
				kbName={kb.name}
				defaultUser={search.user}
			/>
			<BulkMemoryDialog open={dialog === "bulk"} onOpenChange={(o) => setDialog(o ? "bulk" : null)} kb={kb.id} />
			<ViewMemoryDialog kb={kb.id} id={viewing} onClose={() => setViewing(null)} />
			<ConfirmDialog
				open={!!deleting}
				onOpenChange={(o) => !o && setDeleting(null)}
				title={t.deleteTitle}
				body={
					<>
						<p className="m-0 mb-2">“{deleting?.content}”</p>
						<p className="m-0">{t.deleteBody}</p>
					</>
				}
				confirmLabel={t.confirmDelete}
				pending={remove.isPending}
				onConfirm={() =>
					deleting &&
					remove.mutate(deleting.id, {
						onSuccess: () => {
							toast.success(t.deleted);
							setDeleting(null);
							find.reset();
						},
						onError: (e) => toast.error(errorText(e)),
					})
				}
			/>
			<TypedConfirmDialog
				open={dialog === "bulkDelete"}
				onOpenChange={(o) => setDialog(o ? "bulkDelete" : null)}
				title={t.bulkTitle}
				body={<span data-testid="bulk-scope">{bulkScopeText(kb.name, search.mtype, search.user)}</span>}
				word="delete"
				confirmLabel={t.confirmDelete}
				pending={bulkDelete.isPending}
				onConfirm={() =>
					bulkDelete.mutate(
						{ type: search.mtype, user: search.user },
						{
							onSuccess: (r) => {
								toast.success(plural(t.bulkDone, r.deleted));
								setDialog(null);
								find.reset();
							},
							onError: (e) => toast.error(errorText(e)),
						},
					)
				}
			/>
			<TypedConfirmDialog
				open={dialog === "purge"}
				onOpenChange={(o) => setDialog(o ? "purge" : null)}
				title={t.purgeTitle}
				body={fill(t.purgeBody, { kb: kb.name })}
				word="delete"
				confirmLabel={t.confirmPurge}
				pending={purge.isPending}
				onConfirm={() =>
					purge.mutate(undefined, {
						onSuccess: (r) => {
							toast.success(plural(t.purgeDone, r.deleted));
							setDialog(null);
						},
						onError: (e) => toast.error(errorText(e)),
					})
				}
			/>
		</>
	);
}
