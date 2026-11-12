import { Loader2 } from "lucide-react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import type { VersionSummary } from "./api";

export interface NumberedVersion extends VersionSummary {
	number: number;
}

/** Versions numbered by creation time (v1 = oldest); the API has no version number. */
export function numberVersions(versions: VersionSummary[]): NumberedVersion[] {
	return [...versions]
		.sort((a, b) => a.created_at.localeCompare(b.created_at))
		.map((v, i) => ({ ...v, number: i + 1 }));
}

export function VersionTable({
	versions,
	viewing,
	restoringId,
	onView,
	onRestore,
}: {
	versions: NumberedVersion[];
	viewing: string | null;
	restoringId: string | null;
	onView: (v: NumberedVersion) => void;
	onRestore: (v: NumberedVersion) => void;
}) {
	const rows = [...versions].reverse();
	const columns: Column<NumberedVersion>[] = [
		{
			id: "version",
			header: t.versions.colVersion,
			cell: (v) => (
				<b className="font-mono">
					v{v.number}
					{v.id === viewing ? <span className="sr-only"> (shown)</span> : null}
				</b>
			),
		},
		{ id: "created", header: t.versions.colCreated, cell: (v) => formatDateTime(v.created_at), stackLabel: true },
		{
			id: "status",
			header: t.versions.colStatus,
			cell: (v) =>
				v.status === "active" ? (
					<StatusBadge status="active" label={t.versions.active} />
				) : (
					<StatusBadge status="inactive" label={t.versions.inactive} />
				),
		},
		{
			id: "chunks",
			header: t.versions.colChunks,
			align: "right",
			cell: (v) => formatNumber(v.chunk_count ?? 0),
			stackLabel: true,
		},
		{ id: "parser", header: t.versions.colParser, cell: (v) => v.parser_version ?? "—", stackLabel: true },
		{ id: "schema", header: t.versions.colSchema, cell: (v) => v.chunk_schema_version ?? "—", stackLabel: true },
		{
			id: "actions",
			header: "",
			cell: (v) => (
				<div className="flex flex-wrap justify-end gap-2 max-[600px]:justify-start">
					<Button
						size="sm"
						variant="outline"
						aria-label={`${t.versions.view}: v${v.number}`}
						aria-pressed={v.id === viewing}
						onClick={() => onView(v)}
					>
						{t.versions.view}
					</Button>
					{v.status === "active" ? null : (
						<Button
							size="sm"
							variant="default"
							aria-label={`${t.versions.makeActive}: v${v.number}`}
							disabled={restoringId !== null}
							onClick={() => onRestore(v)}
						>
							{restoringId === v.id ? <Loader2 className="animate-spin" aria-hidden /> : null}
							{restoringId === v.id ? t.versions.rebuilding : t.versions.makeActive}
						</Button>
					)}
				</div>
			),
		},
	];
	return (
		<DataTable
			caption={t.versions.caption}
			columns={columns}
			rows={rows}
			rowKey={(v) => v.id}
			empty={t.versions.none}
		/>
	);
}

export const versionLabel = (n: number) => fill(t.passages.version, { n });
