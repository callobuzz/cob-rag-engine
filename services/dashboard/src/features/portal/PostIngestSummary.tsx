import { Link } from "@tanstack/react-router";
import { CircleCheck, FileText } from "lucide-react";
import { KeyValue } from "@/components/patterns/KeyValue";
import { buttonVariants } from "@/components/ui/button";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import type { FinalizeResult } from "./api";

const s = t.summary;

/** What a finalized post-ingest review produced (FinalizeReviewResponse), with "Open document". */
export function PostIngestSummary({ result, kb }: { result: FinalizeResult; kb: string }) {
	const rows = [
		{ label: s.newVersion, value: <code className="font-mono text-[12.5px]">{result.new_version_id}</code> },
		{ label: s.inNewVersion, value: formatNumber(result.chunks_in_new_version) },
		{ label: s.unchanged, value: formatNumber(result.chunks_unchanged) },
		{ label: s.edited, value: formatNumber(result.chunks_edited) },
		{ label: s.added, value: formatNumber(result.chunks_added) },
		{ label: s.parents, value: formatNumber(result.parent_chunks) },
		{ label: s.rejected, value: formatNumber(result.staged_chunks_rejected) },
		{ label: s.dropped, value: formatNumber(result.previous_chunks_dropped) },
		{ label: s.discarded, value: formatNumber(result.discarded_parent_edits?.length ?? 0) },
	];
	const discarded = result.discarded_parent_edits ?? [];
	return (
		<section
			aria-labelledby="post-ingest-summary"
			data-testid="post-ingest-summary"
			className="grid gap-3 rounded-lg border border-[color-mix(in_srgb,var(--success)_40%,var(--border))] bg-card"
		>
			<div className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3.5">
				<div className="flex gap-2.5">
					<CircleCheck className="mt-0.5 size-5 shrink-0 text-success" aria-hidden />
					<div>
						<h2 id="post-ingest-summary" className="text-[15px] font-bold">
							{s.title}
						</h2>
						<p className="m-0 text-[12.5px] text-muted-foreground">{s.lead}</p>
					</div>
				</div>
				<Link
					to="/documents/$documentId"
					params={{ documentId: result.document_id }}
					search={{ kb, tab: "versions" }}
					className={buttonVariants({ variant: "default" })}
				>
					<FileText aria-hidden />
					{s.open}
				</Link>
			</div>
			<KeyValue rows={rows} />
			{discarded.length ? (
				<div className="grid gap-1.5 px-4 pb-4 text-[12.5px]">
					<b>{s.discarded}</b>
					<p className="m-0 text-foreground-2">{s.discardedLead}</p>
					<ul className="m-0 grid gap-1 pl-[18px]">
						{discarded.map((d) => (
							<li key={d.staged_chunk_id} className="break-words">
								{fill(s.discardedItem, {
									chunk: d.staged_chunk_id.slice(0, 8),
									parent: d.parent_chunk_id.slice(0, 8),
									reason: d.reason,
								})}
							</li>
						))}
					</ul>
				</div>
			) : null}
		</section>
	);
}
