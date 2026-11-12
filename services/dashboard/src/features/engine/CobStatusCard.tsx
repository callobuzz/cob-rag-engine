import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, Loader2, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { engineStrings } from "@/lib/strings/admin";
import { type CobStatus, cobStatusQuery, useCobResync } from "./api";

const t = engineStrings.cob;

interface EvalSummary {
	passed: boolean;
	hit: number | null;
	hitIndic: number | null;
	leaks: number;
	negatives: number;
	error: string | null;
}

/** Reads the post-sync evaluation summary the worker stores (`cob_status.eval`); null when absent. */
export function readEval(summary: CobStatus["eval"]): EvalSummary | null {
	if (!summary) return null;
	const num = (v: unknown) => (typeof v === "number" ? v : null);
	return {
		passed: summary.passed === true,
		hit: num(summary.hit_at_5),
		hitIndic: num(summary.hit_at_5_indic),
		leaks: num(summary.role_leaks) ?? 0,
		negatives: num(summary.negatives_failed) ?? 0,
		error: typeof summary.error === "string" ? summary.error : null,
	};
}

const percent = (v: number | null) => (v === null ? "n/a" : `${Math.round(v * 100)}%`);

function evalLabel(summary: EvalSummary | null): string {
	if (!summary) return t.evalNone;
	if (summary.error) return t.evalProblems.failed;
	if (summary.passed) return t.evalPassed(percent(summary.hit));
	return t.evalBelow;
}

function evalProblems(summary: EvalSummary): string[] {
	if (summary.error) return [t.evalProblems.failed];
	const out: string[] = [];
	if (summary.leaks > 0) out.push(t.evalProblems.leaks(summary.leaks));
	if (summary.negatives > 0) out.push(t.evalProblems.negatives(summary.negatives));
	if (summary.hit !== null && summary.hit < 0.8)
		out.push(t.evalProblems.hits(percent(summary.hit), percent(summary.hitIndic)));
	else if (summary.hitIndic !== null && summary.hitIndic < 0.7)
		out.push(t.evalProblems.hits(percent(summary.hit), percent(summary.hitIndic)));
	return out;
}

/**
 * COB's docs on Engine info (operator only): which docs version the built-in assistant has read,
 * whether that is the version shipped with this engine, the last sync and a Resync button.
 */
export function CobStatusCard() {
	const q = useQuery(cobStatusQuery);
	const resync = useCobResync();
	const [confirming, setConfirming] = useState(false);
	const status = q.data;
	const evaluation = readEval(status?.eval ?? null);
	const busy = status?.sync_state === "queued" || status?.sync_state === "running";

	const run = () => {
		resync.mutate(undefined, {
			onSuccess: (r) => {
				setConfirming(false);
				if (r.queued) toast.success(t.queued, { description: t.queuedBody });
				else toast.info(t.alreadyRunning);
			},
			onError: (e) => {
				setConfirming(false);
				toast.error(t.startFailed, { description: messageFor(toApiError(e)).title });
			},
		});
	};

	return (
		<Panel
			title={t.title}
			sub={t.sub}
			aside={
				<div className="flex items-center gap-2">
					{status ? (
						<Badge variant={status.up_to_date ? "ok" : "warn"}>
							{status.up_to_date ? <CircleCheck aria-hidden /> : <CircleAlert aria-hidden />}
							{status.up_to_date ? t.current : t.pending}
						</Badge>
					) : null}
					<Button size="sm" disabled={!status || busy || resync.isPending} onClick={() => setConfirming(true)}>
						<RefreshCw aria-hidden />
						{t.resync}
					</Button>
				</div>
			}
		>
			{q.isPending ? (
				<CardSkeleton lines={4} />
			) : q.isError ? (
				<div className="p-3">
					<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : status ? (
				<>
					{busy ? (
						<p role="status" className="m-0 flex items-center gap-2 border-b border-border px-4 py-3 text-[13px]">
							<Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />
							<span>
								<b>{t.syncing}</b> {t.syncingBody}
							</span>
						</p>
					) : null}
					<KeyValue
						rows={[
							{ label: t.version, value: status.docs_version ?? t.never, muted: !status.docs_version },
							{ label: t.documents, value: formatNumber(status.document_count) },
							{
								label: t.lastSync,
								value: status.synced_at ? formatRelative(status.synced_at) : t.never,
								muted: !status.synced_at,
							},
							{ label: t.state, value: t.states[status.sync_state] ?? status.sync_state },
							{ label: t.eval, value: evalLabel(evaluation), muted: !evaluation },
						]}
					/>
					{evaluation && !evaluation.passed ? (
						<div className="flex flex-wrap items-center gap-2 border-t border-border px-4 py-3 text-[13px]">
							<Badge variant="warn">
								<CircleAlert aria-hidden />
								{t.evalBelow}
							</Badge>
							<span>{evalProblems(evaluation).join(" · ")}</span>
							<a className="font-semibold underline" href="#cob-review">
								{t.evalReview}
							</a>
						</div>
					) : null}
					{status.sync_state === "failed" && status.last_error ? (
						<details className="border-t border-border px-4 py-3 text-[13px]">
							<summary className="cursor-pointer font-semibold text-destructive">
								<Badge variant="bad">{t.states.failed}</Badge> {t.error}
							</summary>
							<p className="mt-2 break-words font-mono text-[12px]">{status.last_error}</p>
						</details>
					) : null}
				</>
			) : null}
			<ConfirmDialog
				open={confirming}
				onOpenChange={setConfirming}
				title={t.resyncTitle}
				body={t.resyncBody}
				confirmLabel={t.resyncConfirm}
				destructive={false}
				pending={resync.isPending}
				onConfirm={run}
			/>
		</Panel>
	);
}
