import { Badge } from "@/components/ui/badge";
import { Panel } from "@/features/kbs/ui";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import { formatMs, pipelineCells } from "./stages";

/**
 * Mockup `.pipe`: one cell per stage with its time; a stage missing from `stage_timings_ms` is
 * drawn dashed "off"; the slowest stage is amber (with the word "slowest" for colour-blind
 * readers). A visually hidden table carries the same data for screen readers.
 */
export function PipelineStrip({
	timings,
	writeMs,
	totalMs,
	state,
}: {
	timings: Record<string, number> | null | undefined;
	writeMs: number | null;
	totalMs: number | null;
	state: "pending" | "ok" | "error" | "skipped" | "none";
}) {
	const show = state === "ok";
	const cells = show ? pipelineCells(timings, writeMs) : [];
	return (
		<Panel
			title={t.pipeline.title}
			sub={totalMs != null ? fill(t.pipeline.sub, { t: formatMs(totalMs) }) : t.pipeline.subEmpty}
			aside={<Badge>{t.pipeline.off}</Badge>}
		>
			{!show ? (
				<p className="m-0 px-4 py-6 text-[13px] text-muted-foreground">
					{state === "error" ? t.sources.unavailable : t.pipeline.empty}
				</p>
			) : (
				<>
					<div
						aria-hidden
						data-testid="pipeline"
						className="grid grid-cols-3 gap-x-0 gap-y-3.5 px-4 py-3.5 min-[521px]:grid-cols-6"
					>
						{cells.map((c) => (
							<div
								key={c.key}
								data-testid="pipeline-cell"
								data-state={c.ms === null ? "off" : c.slow ? "slow" : "on"}
								className="grid min-w-0 content-start gap-1.5"
							>
								<div
									className={cn("mr-[3px] h-1.5 rounded-[3px] bg-primary", c.slow && "bg-warning")}
									style={
										c.ms === null
											? { background: "repeating-linear-gradient(90deg, var(--input) 0 6px, transparent 6px 10px)" }
											: undefined
									}
								/>
								<b className="pr-2 text-xs leading-tight font-semibold break-words">{c.label}</b>
								<span className={cn("pr-2 font-mono text-[11px] text-muted-foreground", c.slow && "text-warning")}>
									{c.ms === null ? t.pipeline.offCell : formatMs(c.ms)}
									{c.slow ? ` · ${t.pipeline.slowest}` : ""}
								</span>
							</div>
						))}
					</div>
					<table className="sr-only">
						<caption>{t.pipeline.tableCaption}</caption>
						<thead>
							<tr>
								<th scope="col">{t.pipeline.colStage}</th>
								<th scope="col">{t.pipeline.colTime}</th>
							</tr>
						</thead>
						<tbody>
							{cells.map((c) => (
								<tr key={c.key}>
									<th scope="row">{c.label}</th>
									<td>
										{c.ms === null ? t.pipeline.offCell : formatMs(c.ms)}
										{c.slow ? ` (${t.pipeline.slowest})` : ""}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</>
			)}
		</Panel>
	);
}
