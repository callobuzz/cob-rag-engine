import { type Status, StatusBadge } from "@/components/patterns/StatusBadge";
import { formatNumber } from "@/lib/format/number";
import { migrationsStrings as t } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import type { Migration } from "./api";

const BADGE: Record<string, Status> = {
	pending_approval: "pending",
	queued: "queued",
	running: "running",
	verifying: "running",
	rolling_back: "running",
	completed: "completed",
	rolled_back: "suspended",
	cancelled: "suspended",
	failed: "failed",
};

export function statusLabel(status: string): string {
	return t.status[status] ?? status;
}

/** Status as icon + text (never colour alone). */
export function MigrationStatusBadge({ status }: { status: string }) {
	return <StatusBadge status={BADGE[status] ?? status} label={statusLabel(status)} />;
}

export function strategyLabel(strategy: string): string {
	return t.strategy[strategy] ?? strategy.replace(/_/g, " ");
}

/** The model name in a migration's `from_config` / `to_config`. */
export function modelOf(config: Record<string, unknown>): string {
	return typeof config.model === "string" ? config.model : "—";
}

export function dimsOf(config: Record<string, unknown>): string {
	return typeof config.dimensions === "number" ? formatNumber(config.dimensions) : "—";
}

/**
 * A progress bar with its value in text ("1,240 of 3,100"); `role="progressbar"` so the value is
 * announced. Nothing to do (total 0) shows the text only.
 */
export function Progress({ label, done, total }: { label: string; done: number; total: number }) {
	const text = fill(t.progressOf, { done: formatNumber(done), total: formatNumber(total) });
	const share = total > 0 ? Math.min(1, done / total) : 0;
	return (
		<div className="grid min-w-[140px] gap-1">
			<div className="flex justify-between gap-2 text-[12px] text-foreground-2">
				<span>{label}</span>
				<span className="tabular-nums">{text}</span>
			</div>
			{total > 0 ? (
				<div
					role="progressbar"
					aria-label={label}
					aria-valuemin={0}
					aria-valuemax={total}
					aria-valuenow={Math.min(done, total)}
					aria-valuetext={text}
					className="h-1.5 overflow-hidden rounded-[3px] bg-secondary"
				>
					<i
						className={cn("block h-full rounded-[3px]", done >= total ? "bg-success" : "bg-primary")}
						style={{ width: `${Math.round(share * 1000) / 10}%` }}
					/>
				</div>
			) : null}
		</div>
	);
}

/** Chunks, and memories when the knowledge base has any. */
export function MigrationProgress({ m }: { m: Migration }) {
	return (
		<div className="grid gap-1.5">
			<Progress label={t.passages} done={m.chunks_done} total={m.chunks_total} />
			{m.memories_total > 0 ? <Progress label={t.memories} done={m.memories_done} total={m.memories_total} /> : null}
		</div>
	);
}
