import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ReconciliationFinding } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate, formatRelative } from "@/lib/format/date";
import { ledgerStrings as ls } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { Panel } from "../../kbs/ui";
import { reconciliationsQuery, useRunReconciliation } from "./api";

const s = ls.reconciliation;

function findingText(f: ReconciliationFinding): string {
	const known = (s.findings as Record<string, string>)[f.check];
	return known ?? f.check;
}

/**
 * The books check (BIL3), at the top of Billing → Accounts: the last nightly run, whether every
 * balance still equals its transactions, what it found, and "Check now".
 */
export function ReconciliationCard() {
	const q = useQuery(reconciliationsQuery);
	const run = useRunReconciliation();
	const last = q.data?.items[0];
	const problems = last?.findings.filter((f) => !f.informational) ?? [];
	const notes = last?.findings.filter((f) => f.informational) ?? [];

	const check = async () => {
		try {
			await run.mutateAsync();
			toast.info(s.running);
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		}
	};

	let body: React.ReactNode;
	if (q.isPending) body = <CardSkeleton lines={2} />;
	else if (q.isError) body = <ErrorState error={q.error} title={s.loadError} onRetry={() => void q.refetch()} />;
	else if (!last)
		body = (
			<p data-testid="reconciliation-never" className="m-0 p-4 text-foreground-2">
				{s.never}
			</p>
		);
	else
		body = (
			<div className="grid gap-3 p-4">
				<div className="flex flex-wrap items-center gap-3">
					<Badge variant={last.ok ? "ok" : "bad"} data-testid="reconciliation-status">
						{last.ok ? s.ok : s.failed}
					</Badge>
					<span className="text-[13px] text-foreground-2">
						{fill(s.lastRun, { when: formatRelative(last.ran_at) })}, {formatDate(last.day)}
					</span>
					<span className="text-[13px] text-muted-foreground">
						{last.checked_accounts === 1 ? s.customersOne : fill(s.customers, { count: String(last.checked_accounts) })}
					</span>
				</div>
				{problems.length === 0 ? <p className="m-0 text-[13px] text-foreground-2">{s.noFindings}</p> : null}
				{problems.length > 0 ? (
					<ul data-testid="reconciliation-findings" className="m-0 grid gap-1.5 pl-5 text-[13px]">
						{problems.map((f, i) => (
							// biome-ignore lint/suspicious/noArrayIndexKey: findings have no id and never reorder
							<li key={`${f.check}-${i}`}>{findingText(f)}</li>
						))}
					</ul>
				) : null}
				{notes.length > 0 ? (
					<div className="grid gap-1 text-[12.5px] text-muted-foreground">
						<strong className="font-semibold">{s.informational}</strong>
						<ul className="m-0 grid gap-1 pl-5">
							{notes.map((f, i) => (
								// biome-ignore lint/suspicious/noArrayIndexKey: findings have no id and never reorder
								<li key={`${f.check}-${i}`}>{findingText(f)}</li>
							))}
						</ul>
					</div>
				) : null}
			</div>
		);

	return (
		<div data-testid="reconciliation-card">
			<Panel
				title={s.title}
				sub={s.lead}
				aside={
					<Button type="button" size="sm" disabled={run.isPending} onClick={() => void check()}>
						{s.runNow}
					</Button>
				}
			>
				{body}
			</Panel>
		</div>
	);
}
