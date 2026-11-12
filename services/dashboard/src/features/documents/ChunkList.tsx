import { useId, useState } from "react";
import { Pager } from "@/components/patterns/Pager";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format/number";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { CHUNK_PAGE_SIZE, type ChunkView } from "./api";

/**
 * Read-only passages of one version (chunk editing is the Content Portal's job, D11): index,
 * section path, page, tokens, table/code badges, parent link and the text collapsed to 6 lines.
 */
export function ChunkList({
	chunks,
	total,
	page,
	onPage,
	versionLabel,
}: {
	chunks: ChunkView[];
	total: number;
	page: number;
	onPage: (page: number) => void;
	versionLabel: string;
}) {
	const byId = new Map(chunks.map((c) => [c.id, c]));
	if (total === 0) return <p className="m-0 p-4 text-foreground-2">{t.passages.none}</p>;
	return (
		<div className="grid">
			<ol aria-label={fill(t.passages.caption, { version: versionLabel })} className="m-0 grid list-none p-0">
				{chunks.map((c) => (
					<ChunkItem key={c.id} chunk={c} parent={c.parent_chunk_id ? (byId.get(c.parent_chunk_id) ?? null) : null} />
				))}
			</ol>
			{total > CHUNK_PAGE_SIZE ? (
				<div className="border-t border-border">
					<Pager page={page} pageSize={CHUNK_PAGE_SIZE} total={total} onPage={onPage} />
				</div>
			) : null}
		</div>
	);
}

function ChunkItem({ chunk, parent }: { chunk: ChunkView; parent: ChunkView | null }) {
	const [open, setOpen] = useState(false);
	const textId = useId();
	const long = chunk.text.length > 420 || chunk.text.split("\n").length > 6;
	const n = (chunk.chunk_index ?? 0) + 1;
	return (
		<li id={`chunk-${chunk.id}`} className="grid gap-2 border-b border-border px-4 py-3.5 last:border-b-0">
			<div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-muted-foreground">
				<b className="font-mono text-[12px] text-foreground">{fill(t.passages.index, { n: formatNumber(n) })}</b>
				{chunk.section?.length ? <span className="min-w-0 break-words">{chunk.section.join(" › ")}</span> : null}
				{chunk.page != null ? <span>{fill(t.passages.page, { n: formatNumber(chunk.page) })}</span> : null}
				{chunk.token_count != null ? (
					<span>{fill(t.passages.tokens, { n: formatNumber(chunk.token_count) })}</span>
				) : null}
				{chunk.is_table ? <Badge variant="info">{t.passages.table}</Badge> : null}
				{chunk.is_code ? <Badge variant="info">{t.passages.code}</Badge> : null}
				{chunk.parent_chunk_id ? (
					parent ? (
						<a href={`#chunk-${parent.id}`} className="font-semibold text-primary">
							{fill(t.passages.parent, { n: formatNumber((parent.chunk_index ?? 0) + 1) })}
						</a>
					) : (
						<span>{t.passages.parentElsewhere}</span>
					)
				) : null}
			</div>
			<p
				id={textId}
				className={`m-0 text-[13.5px] break-words whitespace-pre-wrap text-foreground ${long && !open ? "line-clamp-6" : ""}`}
			>
				{chunk.text}
			</p>
			{long ? (
				<Button
					size="sm"
					variant="ghost"
					className="justify-self-start"
					aria-expanded={open}
					aria-controls={textId}
					onClick={() => setOpen((o) => !o)}
				>
					{open ? t.passages.showLess : t.passages.showAll}
				</Button>
			) : null}
		</li>
	);
}
