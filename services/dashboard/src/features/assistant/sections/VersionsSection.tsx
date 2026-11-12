import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { KbErrorAlert } from "../../kbs/KbErrorAlert";
import { Panel } from "../../kbs/ui";
import { assistantVersionQuery, assistantVersionsQuery, useRestoreVersion } from "../api";
import type { AssistantState, AssistantVersionSummary } from "../types";

const PAGE_SIZE = 20;
const SECTION_KEYS = [
	"identity",
	"voice",
	"language",
	"scope",
	"rules",
	"conversation",
	"leads",
	"handoff",
	"advanced",
] as const;

/** Which sections differ between a version and the live one (by value). */
function changedSections(a: Record<string, unknown>, b: Record<string, unknown>): string[] {
	return SECTION_KEYS.filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k])).map((k) => t.sections[k]);
}

function ViewDialog({
	kb,
	version,
	live,
	onClose,
}: {
	kb: string;
	version: number | null;
	live: AssistantState["published"];
	onClose: () => void;
}) {
	const q = useQuery({ ...assistantVersionQuery(kb, version ?? 0), enabled: version !== null });
	const diff = q.data && live ? changedSections(q.data.config, live.config) : [];
	return (
		<Dialog open={version !== null} onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-w-[760px]">
				<DialogHeader>
					<DialogTitle>{fill(t.versions.viewTitle, { n: version ?? 0 })}</DialogTitle>
				</DialogHeader>
				{q.isError ? <KbErrorAlert error={q.error} /> : null}
				{q.data ? (
					<div className="grid gap-3">
						<p className="m-0 text-[13px]">
							<b>{t.versions.changes}: </b>
							{live && q.data.version === live.version
								? t.versions.noChanges
								: diff.length
									? fill(t.versions.changedSections, { list: diff.join(", ") })
									: t.versions.noChanges}
						</p>
						<Textarea
							readOnly
							rows={16}
							aria-label={t.versions.settings}
							className="font-mono text-[12px] leading-[1.5]"
							value={JSON.stringify(q.data.config, null, 2)}
						/>
					</div>
				) : null}
			</DialogContent>
		</Dialog>
	);
}

/** Version history (design 4.9): view any version, restore an older one as a new live version. */
export function VersionsSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const [page, setPage] = useState(1);
	const [viewing, setViewing] = useState<number | null>(null);
	const [restoring, setRestoring] = useState<number | null>(null);
	const [error, setError] = useState<unknown>(null);
	const q = useQuery(assistantVersionsQuery(kb.id, { offset: (page - 1) * PAGE_SIZE, limit: PAGE_SIZE }));
	const restore = useRestoreVersion(kb.id);
	const liveVersion = state.published?.version ?? null;

	const columns: Column<AssistantVersionSummary>[] = [
		{
			id: "version",
			header: t.versions.version,
			rowHeader: true,
			cell: (r) => (
				<span className="inline-flex flex-wrap items-center gap-2">
					{fill(t.versions.versionCell, { n: r.version })}
					<Badge variant={r.version === liveVersion ? "ok" : "default"}>
						{r.version === liveVersion ? t.versions.live : t.versions.earlier}
					</Badge>
				</span>
			),
		},
		{
			id: "published",
			header: t.versions.published,
			cell: (r) => (r.published_at ? formatDateTime(r.published_at) : ""),
		},
		{ id: "by", header: t.versions.by, cell: (r) => r.created_by },
		{ id: "note", header: t.versions.note, cell: (r) => r.note ?? "" },
		{
			id: "restored",
			header: t.versions.restoredFrom,
			cell: (r) => (r.restored_from ? fill(t.versions.restoredCell, { n: r.restored_from }) : ""),
		},
		{ id: "state", header: t.versions.state, cell: (r) => (r.enabled ? t.versions.on : t.versions.off) },
		{
			id: "actions",
			header: "",
			align: "right",
			cell: (r) => (
				<span className="inline-flex gap-1.5">
					<Button size="sm" variant="outline" onClick={() => setViewing(r.version)}>
						{t.versions.view}
					</Button>
					{r.version !== liveVersion ? (
						<OwnerGate action="assistant.publish">
							<Button size="sm" variant="outline" onClick={() => setRestoring(r.version)}>
								{t.versions.restore}
							</Button>
						</OwnerGate>
					) : null}
				</span>
			),
		},
	];

	const doRestore = async () => {
		if (restoring === null) return;
		setError(null);
		try {
			const out = await restore.mutateAsync(restoring);
			toast.success(
				fill(t.versions.restoredToast, { from: out.restored_from ?? restoring, version: out.version ?? 0 }),
			);
		} catch (e) {
			setError(toApiError(e));
		} finally {
			setRestoring(null);
		}
	};

	return (
		<Panel title={t.versions.title} sub={t.versions.sub}>
			<div className="grid gap-3 p-4">
				{error ? <KbErrorAlert error={error} /> : null}
				{q.isPending ? <TableSkeleton rows={5} /> : null}
				{q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
				{q.data ? (
					<>
						<DataTable
							caption={t.versions.caption}
							columns={columns}
							rows={q.data.items}
							rowKey={(r) => String(r.version)}
							empty={<p className="m-0 p-4 text-[13px] text-muted-foreground">{t.versions.empty}</p>}
						/>
						{q.data.total > PAGE_SIZE ? (
							<Pager page={page} pageSize={PAGE_SIZE} total={q.data.total} onPage={setPage} />
						) : null}
					</>
				) : null}
			</div>
			<ViewDialog kb={kb.id} version={viewing} live={state.published} onClose={() => setViewing(null)} />
			<ConfirmDialog
				open={restoring !== null}
				onOpenChange={(o) => {
					if (!o) setRestoring(null);
				}}
				title={fill(t.versions.restoreTitle, { n: restoring ?? 0 })}
				body={fill(t.versions.restoreBody, {
					next: (liveVersion ?? 0) + 1,
					state: state.enabled ? t.overview.stateOn : t.overview.stateOff,
				})}
				confirmLabel={fill(t.versions.restoreConfirm, { n: restoring ?? 0 })}
				destructive={false}
				pending={restore.isPending}
				onConfirm={() => void doRestore()}
			/>
		</Panel>
	);
}
