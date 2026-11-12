import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CircleCheck, CircleX } from "lucide-react";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { documentsQuery } from "@/features/documents/api";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { isTerminalRun, kbEvalRunQuery, samplesQuery } from "@/features/quality/evals-api";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { setupStrings as t } from "@/lib/strings/setup";
import { type SetupSession, setupGapsQuery, useRunSetupTests } from "../api";

/** The setup's own runs are short and plan-only; poll them faster than the Quality tab does. */
const POLL_MS = 2000;

/**
 * Step 5: how the draft did on the automatic checks, and what the documents do not cover. A
 * check that needed an answer from a document and found none is a gap, with ways to fill it.
 */
export function TestsStep({ kb, session, onContinue }: { kb: string; session: SetupSession; onContinue: () => void }) {
	const runId = session.eval_run_id ?? "";
	const run = useQuery({
		...kbEvalRunQuery(kb, runId),
		enabled: !!runId,
		refetchInterval: (q) => (isTerminalRun(q.state.data) ? false : POLL_MS),
	});
	const done = isTerminalRun(run.data);
	const samples = useQuery({ ...samplesQuery(kb, runId, {}), enabled: !!runId && done });
	const gaps = useQuery({ ...setupGapsQuery(kb, session.id), enabled: !!runId });
	const docs = useQuery(documentsQuery(kb, { limit: 1 }));
	const again = useRunSetupTests(kb, session.id);
	const [error, setError] = useState<unknown>(null);

	if (run.isPending) return <CardSkeleton lines={6} />;
	if (run.isError) return <ErrorState error={run.error} onRetry={() => void run.refetch()} />;
	const totals = run.data.totals;
	const stillRunning = error != null && toApiError(error).code === "eval_run_in_progress";
	const noDocuments = docs.data?.count === 0;
	return (
		<div className="grid gap-4 rounded-lg border border-border bg-card p-5">
			<div className="grid gap-1">
				<h2 className="m-0 text-lg font-semibold">{t.tests.heading}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">
					{done && totals ? fill(t.tests.lead, { cases: Number(totals.questions) }) : t.tests.running}
				</p>
			</div>
			{noDocuments ? (
				<p role="status" className="m-0 rounded-md bg-warning-soft p-3 text-[13px] text-warning">
					{t.tests.noDocuments}{" "}
					<Link to="/add" search={{ kb }} className="font-semibold underline">
						{t.tests.noDocumentsLink}
					</Link>
				</p>
			) : null}
			{run.data.status === "failed" ? (
				<p role="alert" className="m-0 rounded-md bg-destructive-soft p-3 text-[13px] text-destructive">
					{t.tests.failedRun}
					{run.data.error_message ? ` ${run.data.error_message}` : ""}
				</p>
			) : null}
			{stillRunning ? (
				<p role="status" className="m-0 text-[13px] text-muted-foreground">
					{t.tests.stillRunning}
				</p>
			) : error ? (
				<KbErrorAlert error={error} />
			) : null}
			{done && totals ? (
				<p role="status" className="m-0 text-[15px] font-semibold">
					{fill(t.tests.summary, { passed: Number(totals.passed), total: Number(totals.questions) })}
				</p>
			) : null}
			{samples.isError ? <ErrorState error={samples.error} onRetry={() => void samples.refetch()} /> : null}
			{samples.data?.items.length ? (
				<section
					aria-label={t.tests.captionRows}
					// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-reachable (WCAG 2.1.1)
					tabIndex={0}
					className="max-h-[360px] overflow-auto rounded-md border border-border focus-visible:outline-2 focus-visible:outline-ring"
				>
					<table className="w-full text-left text-[13.5px]">
						<caption className="sr-only">{t.tests.captionRows}</caption>
						<thead>
							<tr className="border-b border-border bg-muted text-[12.5px] text-muted-foreground">
								<th scope="col" className="px-3 py-2 font-semibold">
									{t.tests.colQuestion}
								</th>
								<th scope="col" className="px-3 py-2 font-semibold">
									{t.tests.colExpected}
								</th>
								<th scope="col" className="px-3 py-2 font-semibold">
									{t.tests.colResult}
								</th>
							</tr>
						</thead>
						<tbody>
							{samples.data.items.map((s) => {
								const expected = (s.behaviour as { expected?: string } | null)?.expected ?? "";
								return (
									<tr key={s.id} className="border-b border-border align-top last:border-0">
										<td className="px-3 py-2 break-words">{s.question}</td>
										<td className="px-3 py-2">{t.tests.expected[expected] ?? expected}</td>
										<td className="px-3 py-2">
											<span className="inline-flex items-center gap-1.5 font-semibold">
												{s.passed ? (
													<CircleCheck className="size-4 text-success" aria-hidden />
												) : (
													<CircleX className="size-4 text-destructive" aria-hidden />
												)}
												{s.passed ? t.tests.passed : t.tests.failed}
											</span>
										</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				</section>
			) : null}
			{done && gaps.data ? (
				<div className="grid gap-2">
					<h3 className="m-0 text-[15px] font-semibold">{t.tests.gaps}</h3>
					{gaps.data.items.length ? (
						<>
							<p className="m-0 text-[13px] text-muted-foreground">{t.tests.gapsLead}</p>
							<ul className="m-0 grid list-none gap-2 p-0">
								{gaps.data.items.map((g) => (
									<li key={g.question} className="grid gap-1 rounded-md border border-border p-3">
										<span className="font-semibold">{g.question}</span>
										<span className="text-[12.5px] text-muted-foreground">{g.message}</span>
										<span className="flex flex-wrap gap-3 text-[13px]">
											<Link to="/add" search={{ kb }} className="font-semibold underline">
												{t.tests.addContent}
											</Link>
											<Link
												to="/kbs/$kbId"
												params={{ kbId: kb }}
												search={{ tab: "assistant", section: "scope" }}
												className="font-semibold underline"
											>
												{t.tests.addAnswer}
											</Link>
										</span>
									</li>
								))}
							</ul>
						</>
					) : (
						<p className="m-0 text-[13px] text-muted-foreground">{t.tests.noGaps}</p>
					)}
				</div>
			) : null}
			<div className="flex flex-wrap items-center justify-between gap-2">
				<Button
					variant="outline"
					disabled={again.isPending || !done}
					onClick={() => {
						setError(null);
						again.mutateAsync().catch(setError);
					}}
				>
					{t.tests.runAgain}
				</Button>
				<Button variant="default" onClick={onContinue}>
					{t.tests.continue}
				</Button>
			</div>
		</div>
	);
}
