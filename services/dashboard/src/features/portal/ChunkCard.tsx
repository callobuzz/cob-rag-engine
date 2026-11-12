import { ArrowDown, ArrowUp, Check, GripVertical, Scissors, Trash2, TriangleAlert, X } from "lucide-react";
import { type DragEvent, useEffect, useId, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { cn } from "@/lib/utils";
import type { Chunk } from "./api";
import { ChunkStatusBadge } from "./ui";

const c = t.chunks;

/** Warnings arrive as a JSON object: a list under any key, or key → message. */
export function warningList(w: Chunk["warnings"]): string[] {
	if (!w) return [];
	const out: string[] = [];
	for (const [k, v] of Object.entries(w)) {
		if (Array.isArray(v)) out.push(...v.map((x) => (typeof x === "string" ? x : JSON.stringify(x))));
		else if (typeof v === "string") out.push(v);
		else if (v === true) out.push(k.replace(/_/g, " "));
		else if (v != null && v !== false) out.push(`${k.replace(/_/g, " ")}: ${JSON.stringify(v)}`);
	}
	return out;
}

/** Metadata shown to people: the review's bookkeeping keys are left out. */
const HIDDEN_META = new Set(["engine_chunk_id", "review_source", "document_version_id", "original_chunk_index"]);

export interface ChunkCardProps {
	chunk: Chunk;
	/** 1-based position shown as "Chunk {n}". */
	n: number;
	canEdit: boolean;
	/** A parent chunk's copy in a post-ingest review: text changes are refused by the engine. */
	parentDerived: boolean;
	selected: boolean;
	busy: boolean;
	onSelect: (on: boolean) => void;
	onSave: (text: string) => void;
	onApprove: () => void;
	onReject: () => void;
	onSplit: () => void;
	onDelete: () => void;
	/** Undefined when reordering is off (a filter, several pages, or no lock). */
	onMoveUp?: () => void;
	onMoveDown?: () => void;
	onDragStart?: (e: DragEvent) => void;
}

/**
 * One chunk as an `article` headed "Chunk {n}": status, source, confidence, warnings, metadata, the
 * text (editable with the lock), and its actions. Status is icon + text, never colour alone.
 */
export function ChunkCard(p: ChunkCardProps) {
	const { chunk, n } = p;
	const [text, setText] = useState(chunk.text);
	const headingId = useId();
	const textId = useId();
	// A refetch after a save (or someone else's change) brings new text: follow it when not editing.
	useEffect(() => setText(chunk.text), [chunk.text]);
	const dirty = text !== chunk.text && text.trim().length > 0;
	const warnings = warningList(chunk.warnings);
	const meta = Object.entries(chunk.metadata_ ?? {}).filter(([k, v]) => !HIDDEN_META.has(k) && v != null);
	const textLocked = !p.canEdit || p.parentDerived;

	return (
		<article
			aria-labelledby={headingId}
			data-testid="chunk-card"
			className={cn(
				"grid gap-3 rounded-lg border bg-card p-4",
				p.selected ? "border-primary shadow-[0_0_0_1px_var(--primary)]" : "border-border",
			)}
		>
			<header className="flex flex-wrap items-center gap-2">
				{p.onDragStart ? (
					<button
						type="button"
						draggable
						onDragStart={p.onDragStart}
						aria-label={fill(c.drag, { n })}
						className="grid size-7 cursor-grab place-items-center rounded-sm text-muted-foreground hover:bg-accent"
					>
						<GripVertical className="size-4" aria-hidden />
					</button>
				) : null}
				<h3 id={headingId} className="font-mono text-[13px] font-bold">
					{fill(c.chunk, { n })}
				</h3>
				<ChunkStatusBadge status={chunk.status} />
				<Badge>{c.source[chunk.source] ?? chunk.source}</Badge>
				{chunk.confidence_score != null ? (
					<Badge variant={chunk.confidence_score < 0.6 ? "warn" : "default"}>
						{fill(c.confidence, { pct: formatPercent(chunk.confidence_score) })}
					</Badge>
				) : null}
				<span className="ml-auto flex items-center gap-2">
					{p.canEdit && !p.parentDerived ? (
						<span className="flex items-center gap-1.5 text-[12.5px] text-foreground-2">
							<Checkbox
								checked={p.selected}
								onCheckedChange={(v) => p.onSelect(v === true)}
								aria-label={fill(c.select, { n })}
							/>
						</span>
					) : null}
					{p.onMoveUp || p.onMoveDown ? (
						<>
							<Button
								size="icon-sm"
								variant="ghost"
								aria-label={fill(c.moveUp, { n })}
								disabled={!p.onMoveUp || p.busy}
								onClick={p.onMoveUp}
							>
								<ArrowUp aria-hidden />
							</Button>
							<Button
								size="icon-sm"
								variant="ghost"
								aria-label={fill(c.moveDown, { n })}
								disabled={!p.onMoveDown || p.busy}
								onClick={p.onMoveDown}
							>
								<ArrowDown aria-hidden />
							</Button>
						</>
					) : null}
				</span>
			</header>

			{warnings.length ? (
				<div className="flex gap-2 rounded-sm bg-warning-soft px-3 py-2 text-[12.5px] text-warning">
					<TriangleAlert className="mt-px size-4 shrink-0" aria-hidden />
					<div>
						<b className="font-semibold">{c.warnings}: </b>
						{warnings.join(" · ")}
					</div>
				</div>
			) : null}

			<div className="grid gap-1.5">
				<label htmlFor={textId} className="sr-only">
					{fill(c.text, { n })}
				</label>
				<Textarea
					id={textId}
					value={text}
					onChange={(e) => setText(e.target.value)}
					readOnly={textLocked}
					rows={Math.min(12, Math.max(3, Math.ceil(text.length / 90)))}
					className={cn("text-[13.5px] leading-[1.55]", textLocked && "bg-secondary")}
				/>
				{p.parentDerived ? <small className="text-[12.5px] text-muted-foreground">{c.parentDerived}</small> : null}
			</div>

			{meta.length ? (
				<details className="text-[12.5px] text-foreground-2">
					<summary className="cursor-pointer font-semibold">{c.metadata}</summary>
					<dl className="mt-1.5 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1">
						{meta.map(([k, v]) => (
							<div key={k} className="contents">
								<dt className="font-mono text-muted-foreground">{k}</dt>
								<dd className="m-0 break-words">{typeof v === "string" ? v : JSON.stringify(v)}</dd>
							</div>
						))}
					</dl>
				</details>
			) : null}

			{p.canEdit ? (
				<div className="flex flex-wrap gap-2">
					{!p.parentDerived ? (
						<Button size="sm" variant="default" disabled={!dirty || p.busy} onClick={() => p.onSave(text.trim())}>
							{c.save}
						</Button>
					) : null}
					{chunk.status !== "approved" ? (
						<Button size="sm" variant="outline" disabled={p.busy} onClick={p.onApprove}>
							<Check aria-hidden />
							{c.approve}
						</Button>
					) : null}
					{chunk.status !== "rejected" ? (
						<Button size="sm" variant="outline" disabled={p.busy} onClick={p.onReject}>
							<X aria-hidden />
							{c.reject}
						</Button>
					) : null}
					{!p.parentDerived ? (
						<Button size="sm" variant="outline" disabled={p.busy || chunk.text.length < 2} onClick={p.onSplit}>
							<Scissors aria-hidden />
							{c.split}
						</Button>
					) : null}
					<Button size="sm" variant="outline" className="text-destructive" disabled={p.busy} onClick={p.onDelete}>
						<Trash2 aria-hidden />
						{c.delete}
					</Button>
				</div>
			) : null}
		</article>
	);
}
