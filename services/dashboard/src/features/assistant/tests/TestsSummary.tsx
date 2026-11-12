import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { kbEvalRunsQuery } from "@/features/quality/evals-api";
import { runResult } from "@/features/quality/runs/format";
import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";

/**
 * The newest check that ran when the assistant was last published ("After publishing v7: 11 of
 * 12 behaviour checks passed"), with a way into the Quality page.
 */
export function TestsSummary({ kb }: { kb: string }) {
	const runs = useQuery(kbEvalRunsQuery(kb, { kind: "golden" }));
	if (runs.isPending) return <CardSkeleton lines={2} />;
	if (runs.isError) return <ErrorState error={runs.error} title={t.tests.error} onRetry={() => void runs.refetch()} />;
	const latest = runs.data.items.find((r) => r.triggered_by === "publish" && r.status === "completed");
	const res = latest ? runResult(latest.totals) : null;
	return (
		<Panel title={t.tests.title}>
			<div className="grid gap-3 p-4">
				{latest && res ? (
					<p className="m-0">
						{fill(t.tests.afterPublish, {
							version: latest.config_version ?? "?",
							passed: res.passed,
							total: res.total,
						})}
					</p>
				) : (
					<p className="m-0 text-foreground-2">{t.tests.none}</p>
				)}
				<div>
					<Link
						to="/quality"
						search={{ kb, tab: latest ? "runs" : "golden", ...(latest ? { run: latest.id } : {}) }}
						className={buttonVariants({ variant: "default" })}
					>
						{t.tests.open}
					</Link>
				</div>
			</div>
		</Panel>
	);
}
