import { Link } from "@tanstack/react-router";
import { buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { formatDate } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { valueStrings as t } from "@/lib/strings/value";
import type { ValueReport } from "./api";
import { formatFirstReply, formatWhole, monthName, timesText } from "./format";
import { ValueFormula } from "./ValueFormula";

/**
 * One month of the value report: the estimate with its formula, the counts, what people asked
 * and what the assistant could not answer. Shared by the owner's page and the deployer's tab, so
 * both read the same numbers in the same words. `addAnswers` shows the "add an answer" links (the
 * owner's; the deployer cannot write into a business's knowledge base).
 */
export function ValueReportView({ report, addAnswers }: { report: ValueReport; addAnswers: boolean }) {
	const i = report.inputs;
	const counts: { key: string; label: string; value: string }[] = [
		{ key: "conversations", label: t.counts.conversations, value: formatNumber(i.conversations) },
		{ key: "answered", label: t.counts.answered, value: formatNumber(i.answered) },
		{ key: "after-hours", label: t.counts.afterHours, value: formatNumber(i.answered_after_hours) },
		{
			key: "leads-qualified",
			label: i.leads_unrated > 0 ? t.counts.leads : t.counts.leadsQualified,
			value: formatNumber(i.leads_qualified),
		},
		{ key: "leads-partial", label: t.counts.leadsPartial, value: formatNumber(i.leads_partial) },
		{ key: "handoffs", label: t.counts.handoffs, value: formatNumber(i.handoffs) },
		{ key: "bookings", label: t.counts.bookings, value: formatNumber(i.bookings) },
		{ key: "first-reply", label: t.counts.firstReply, value: formatFirstReply(i.median_first_reply_ms) },
	];
	return (
		<div className="grid gap-4">
			<Panel
				title={t.totalLabel}
				sub={report.live ? `${monthName(report.month)} · ${t.soFar}` : monthName(report.month)}
			>
				<div className="grid gap-3 p-4">
					<p data-testid="value-total" className="m-0 font-display text-4xl font-bold">
						{formatWhole(report.breakdown.total_minor, report.currency)}
					</p>
					<p className="m-0 text-[13px] text-muted-foreground">
						{t.estimateCaption}
						{report.saved_settings ? "" : ` ${t.notSetCaption}`}
					</p>
					<h3 className="m-0 mt-1 text-[13px] font-semibold">{t.formula.title}</h3>
					<ValueFormula report={report} />
				</div>
			</Panel>
			<Panel title={t.counts.title}>
				<dl className="m-0 grid grid-cols-2 gap-px bg-border min-[700px]:grid-cols-4">
					{counts.map((c) => (
						<div
							key={c.key}
							data-testid={`count-${c.key}`}
							className="flex flex-col-reverse justify-end gap-1 bg-card p-4"
						>
							<dt className="text-[12.5px] text-muted-foreground">{c.label}</dt>
							<dd className="m-0 font-display text-xl font-bold">{c.value}</dd>
						</div>
					))}
				</dl>
				{report.after_hours_since ? (
					<p
						data-testid="after-hours-since"
						className="m-0 border-t border-border p-4 text-[12.5px] text-muted-foreground"
					>
						{fill(t.afterHoursSince, { date: formatDate(report.after_hours_since) })}
					</p>
				) : null}
			</Panel>
			<div className="grid items-start gap-4 min-[1001px]:grid-cols-2">
				<Panel title={t.topQuestions.title}>
					{i.top_questions.length === 0 ? (
						<p className="m-0 p-4 text-[13.5px] text-muted-foreground">{t.topQuestions.empty}</p>
					) : (
						<ul data-testid="top-questions" className="m-0 grid list-none gap-0 p-0">
							{i.top_questions.map((q) => (
								<li
									key={q.question}
									className="flex items-baseline justify-between gap-3 border-b border-border px-4 py-2.5 text-[13.5px] last:border-b-0"
								>
									<span className="min-w-0 break-words">{q.question}</span>
									<span className="shrink-0 text-muted-foreground">{timesText(q.count)}</span>
								</li>
							))}
						</ul>
					)}
				</Panel>
				<Panel title={t.unanswered.title} sub={i.unanswered.length > 0 ? t.unanswered.hint : undefined}>
					{i.unanswered.length === 0 ? (
						<p className="m-0 p-4 text-[13.5px] text-muted-foreground">{t.unanswered.empty}</p>
					) : (
						<ul data-testid="unanswered" className="m-0 grid list-none gap-0 p-0">
							{i.unanswered.map((q) => (
								<li
									key={q.question}
									className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5 text-[13.5px] last:border-b-0"
								>
									<span className="min-w-0 break-words">
										{q.question} <span className="text-muted-foreground">({timesText(q.count)})</span>
									</span>
									{addAnswers ? (
										<Link
											to="/knowledge"
											search={{ tab: "articles", new: "faq", question: q.question }}
											className={buttonVariants({ variant: "outline", size: "sm" })}
										>
											{t.unanswered.add}
										</Link>
									) : null}
								</li>
							))}
						</ul>
					)}
				</Panel>
			</div>
		</div>
	);
}
