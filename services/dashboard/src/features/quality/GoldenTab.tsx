import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Download, Plus, Upload } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { downloadText } from "@/features/insights/download";
import { useErrorText } from "@/features/insights/ui";
import { Panel } from "@/features/kbs/ui";
import type { Corpus } from "@/lib/api/queries";
import { formatDateTime, formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { qualityStrings } from "@/lib/strings/insights";
import { qualityEvalsStrings as evalsT } from "@/lib/strings/quality-evals";
import { exportGolden, GOLDEN_PAGE_SIZE, type GoldenPair, goldenQuery, useDeleteGolden } from "./api";
import { goldenSetsQuery } from "./evals-api";
import { GoldenImport } from "./GoldenImport";
import { GoldenPairDialog } from "./GoldenPairDialog";
import { goldenToCsv } from "./golden-csv";
import { SetsPanel } from "./sets/SetsPanel";

const t = qualityStrings.golden;

/** File name stem: "golden-patient-help-2026-09-30". */
export function exportName(kbName: string, now = new Date()): string {
	const slug =
		kbName
			.toLowerCase()
			.normalize("NFKD")
			.replace(/[^a-z0-9]+/g, "-")
			.replace(/^-+|-+$/g, "") || "kb";
	return `golden-${slug}-${now.toISOString().slice(0, 10)}`;
}

/**
 * Golden set of one knowledge base: table with paging from `total`, add/edit dialog, delete
 * (plain confirm, owners only — A8), Import CSV (template link) and Export (JSON or CSV, built
 * from `GET /golden/export` in the browser).
 */
export function GoldenTab({ kb, page, onPage }: { kb: Corpus; page: number; onPage: (p: number) => void }) {
	const q = useQuery(goldenQuery(kb.id, page));
	const [editing, setEditing] = useState<GoldenPair | "new" | null>(null);
	const [deleting, setDeleting] = useState<GoldenPair | null>(null);
	const [importing, setImporting] = useState(false);
	const [exporting, setExporting] = useState(false);
	const remove = useDeleteGolden(kb.id);
	const sets = useQuery(goldenSetsQuery(kb.id));
	const errorText = useErrorText();

	const doExport = async (kind: "json" | "csv") => {
		setExporting(true);
		try {
			const r = await exportGolden(kb.id);
			const name = exportName(kb.name);
			if (kind === "json") downloadText(JSON.stringify(r.items, null, 2), `${name}.json`, "application/json");
			else
				downloadText(
					goldenToCsv(
						r.items.map((x) => ({
							query: String(x.query ?? ""),
							expected_answer: String(x.expected_answer ?? ""),
							expected_chunk_ids: Array.isArray(x.expected_chunk_ids) ? x.expected_chunk_ids.map(String) : null,
						})),
					),
					`${name}.csv`,
					"text/csv;charset=utf-8",
				);
			toast.success(fill(t.exported, { n: r.count }));
		} catch (e) {
			toast.error(errorText(e));
		} finally {
			setExporting(false);
		}
	};

	const actions = (
		<div className="flex flex-wrap gap-2">
			<Button type="button" variant="default" onClick={() => setEditing("new")}>
				<Plus aria-hidden />
				{t.add}
			</Button>
			<Button type="button" onClick={() => setImporting(true)}>
				<Upload aria-hidden />
				{t.import}
			</Button>
			<DropdownMenu modal={false}>
				<DropdownMenuTrigger asChild>
					<Button type="button" disabled={exporting}>
						<Download aria-hidden />
						{t.export}
						<ChevronDown aria-hidden />
					</Button>
				</DropdownMenuTrigger>
				<DropdownMenuContent align="end">
					<DropdownMenuItem onSelect={() => void doExport("json")}>{t.exportJson}</DropdownMenuItem>
					<DropdownMenuItem onSelect={() => void doExport("csv")}>{t.exportCsv}</DropdownMenuItem>
				</DropdownMenuContent>
			</DropdownMenu>
		</div>
	);

	const columns: Column<GoldenPair>[] = [
		{
			id: "question",
			header: t.colQuestion,
			rowHeader: true,
			className: "min-w-[200px]",
			cell: (p) => <span className="font-semibold">{p.query}</span>,
		},
		{
			id: "answer",
			header: t.colAnswer,
			stackLabel: true,
			className: "min-w-[240px]",
			cell: (p) => (
				<span className="line-clamp-3 whitespace-pre-wrap text-foreground-2">
					{p.expected_answer ?? evalsT.pairs.noAnswer}
				</span>
			),
		},
		{
			id: "behaviour",
			header: evalsT.pairs.colBehaviour,
			stackLabel: true,
			className: "min-w-[140px]",
			cell: (p) =>
				p.expected_behaviour
					? (evalsT.behaviours[p.expected_behaviour as keyof typeof evalsT.behaviours] ?? p.expected_behaviour)
					: evalsT.pairs.noAnswer,
		},
		{
			id: "tags",
			header: evalsT.pairs.colTags,
			stackLabel: true,
			className: "min-w-[140px]",
			cell: (p) => (
				<span className="flex flex-wrap gap-1">
					{[evalsT.origins[p.origin as keyof typeof evalsT.origins] ?? p.origin, p.difficulty, p.topic]
						.filter((x): x is string => !!x)
						.map((x) => (
							<Badge key={x} variant="outline">
								{x}
							</Badge>
						))}
				</span>
			),
		},
		{
			id: "source",
			header: t.colSource,
			stackLabel: true,
			className: "whitespace-nowrap",
			cell: (p) => (t.sources as Record<string, string>)[p.source] ?? p.source,
		},
		{
			id: "created",
			header: t.colCreated,
			stackLabel: true,
			className: "whitespace-nowrap",
			cell: (p) => <time title={formatDateTime(p.created_at)}>{formatRelative(p.created_at)}</time>,
		},
		{
			id: "actions",
			header: "",
			cell: (p) => (
				<div className="flex gap-1.5">
					<Button size="sm" aria-label={fill(t.editFor, { q: p.query })} onClick={() => setEditing(p)}>
						{t.edit}
					</Button>
					<OwnerGate action="golden.delete">
						<Button
							size="sm"
							variant="destructive"
							aria-label={fill(t.deleteFor, { q: p.query })}
							onClick={() => setDeleting(p)}
						>
							{t.delete}
						</Button>
					</OwnerGate>
				</div>
			),
		},
	];

	const dialogs = (
		<>
			<GoldenPairDialog
				open={editing !== null}
				onOpenChange={(o) => !o && setEditing(null)}
				kbId={kb.id}
				kbName={kb.name}
				pair={editing === "new" ? null : editing}
				sets={sets.data?.items}
			/>
			<GoldenImport open={importing} onOpenChange={setImporting} kb={kb} />
			<ConfirmDialog
				open={!!deleting}
				onOpenChange={(o) => !o && setDeleting(null)}
				title={t.deleteTitle}
				body={fill(t.deleteBody, { q: deleting?.query ?? "" })}
				confirmLabel={t.delete}
				pending={remove.isPending}
				onConfirm={() =>
					deleting &&
					remove.mutate(deleting.id, {
						onSuccess: () => {
							toast.success(t.deleted);
							setDeleting(null);
						},
						onError: (e) => {
							setDeleting(null);
							toast.error(errorText(e));
						},
					})
				}
			/>
		</>
	);

	if (q.data && q.data.total === 0 && page === 1) {
		return (
			<>
				<SetsPanel kb={kb.id} />
				<EmptyState title={t.emptyTitle} body={t.emptyBody} primaryAction={actions} />
				{dialogs}
			</>
		);
	}

	return (
		<>
			<SetsPanel kb={kb.id} />
			<Panel title={fill(t.title, { kb: kb.name })} sub={t.sub} aside={actions}>
				{q.isError ? (
					<div className="p-4">
						<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
					</div>
				) : q.isPending ? (
					<div className="p-4">
						<TableSkeleton rows={5} />
					</div>
				) : (
					<>
						<div className="px-2">
							<DataTable
								columns={columns}
								rows={q.data.items}
								rowKey={(p) => p.id}
								caption={fill(t.title, { kb: kb.name })}
							/>
						</div>
						{q.data.total > GOLDEN_PAGE_SIZE ? (
							<Pager page={page} pageSize={GOLDEN_PAGE_SIZE} total={q.data.total} onPage={onPage} />
						) : null}
					</>
				)}
			</Panel>
			{dialogs}
		</>
	);
}
