import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { buttonVariants } from "@/components/ui/button";
import { planMeQuery } from "@/features/kbs/api";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { Panel } from "@/features/kbs/ui";
import { usageQuery } from "@/features/usage/api";
import type { PlanUsageResponse } from "@/lib/api/accounts-types";
import type { Corpus } from "@/lib/api/queries";
import { formatNumber } from "@/lib/format/number";
import type { DayRange } from "@/lib/format/range";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { plural } from "./kpis";
import { HeadLink, List, Row } from "./ui";

const t = overviewStrings.yourKbs;

/** "Pro plan · 2 of 3 knowledge bases" and "7,412 of 10,000 questions this month". */
export function planLines(p: PlanUsageResponse): { plan: string; questions: string } {
	const kbs =
		p.limits.max_knowledge_bases == null
			? fill(t.kbsUnlimited, { used: formatNumber(p.usage.knowledge_bases) })
			: fill(t.kbsOf, {
					used: formatNumber(p.usage.knowledge_bases),
					limit: formatNumber(p.limits.max_knowledge_bases),
				});
	const questions =
		p.limits.monthly_questions == null
			? fill(t.questionsUnlimited, { used: formatNumber(p.usage.questions) })
			: fill(t.questionsLine, {
					used: formatNumber(p.usage.questions),
					limit: formatNumber(p.limits.monthly_questions),
				});
	return { plan: fill(t.planLine, { plan: p.plan.name, kbs }), questions };
}

/** Tenant: questions per knowledge base in the range, and the plan line (→ /plan). */
export function YourKbsCard({ corpora, range, period }: { corpora: Corpus[]; range: DayRange; period: string }) {
	const usage = useQuery(usageQuery({ group_by: "corpus", start: range.start, end: range.end }));
	const plan = useQuery(planMeQuery);
	const by = new Map((usage.data?.rows ?? []).map((r) => [r.key ?? "", r.requests]));
	const rows = [...corpora].sort((a, b) => (by.get(b.id) ?? 0) - (by.get(a.id) ?? 0) || a.name.localeCompare(b.name));
	const lines = plan.data ? planLines(plan.data) : null;
	return (
		<Panel
			title={t.title}
			sub={fill(t.sub, { period })}
			aside={
				<HeadLink to="/kbs" label={t.allLabel}>
					{t.all}
				</HeadLink>
			}
		>
			{usage.isPending ? (
				<TableSkeleton rows={3} />
			) : usage.isError ? (
				<div className="p-4">
					<ErrorState error={usage.error} title={t.error} onRetry={() => void usage.refetch()} />
				</div>
			) : (
				<List label={t.title}>
					{rows.slice(0, 5).map((c) => (
						<Row
							key={c.id}
							testId="your-kb"
							lead={<KbAvatar kb={c} size={28} />}
							title={
								<Link to="/kbs/$kbId" params={{ kbId: c.id }} className="text-foreground no-underline hover:underline">
									{c.name}
								</Link>
							}
							sub={plural(overviewStrings.documents, c.document_count, formatNumber(c.document_count))}
							trail={<b className="font-mono font-medium tabular-nums">{formatNumber(by.get(c.id) ?? 0)}</b>}
						/>
					))}
					{lines ? (
						<Row
							testId="plan-line"
							title={lines.plan}
							sub={lines.questions}
							trail={
								<Link to="/plan" aria-label={t.planLabel} className={buttonVariants({ size: "sm" })}>
									{t.plan}
								</Link>
							}
						/>
					) : null}
				</List>
			)}
		</Panel>
	);
}
