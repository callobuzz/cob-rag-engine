import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import type { RunDetail } from "../evals-api";
import { metricLabel, pct } from "./format";

/** Pass rate and metric means for each origin, difficulty and topic in the set. */
export function TagBreakdown({ run }: { run: RunDetail }) {
	if (run.tags.length === 0) return null;
	const metrics = run.metrics.map((m) => m.metric);
	return (
		<div className="overflow-x-auto">
			<table className="w-full border-collapse text-[13.5px]">
				<caption className="sr-only">{t.detail.tags}</caption>
				<thead>
					<tr className="text-left text-foreground-2">
						<th scope="col" className="px-2 py-1.5">
							{t.detail.tagCols.tag}
						</th>
						<th scope="col" className="px-2 py-1.5">
							{t.detail.tagCols.value}
						</th>
						<th scope="col" className="px-2 py-1.5">
							{t.detail.tagCols.n}
						</th>
						<th scope="col" className="px-2 py-1.5">
							{t.detail.tagCols.pass}
						</th>
						{metrics.map((m) => (
							<th key={m} scope="col" className="px-2 py-1.5">
								{metricLabel(m)}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{run.tags.map((g) => (
						<tr key={`${g.tag}:${g.value}`} className="border-t border-border">
							<th scope="row" className="px-2 py-1.5 text-left font-medium">
								{g.tag}
							</th>
							<td className="px-2 py-1.5">{g.value}</td>
							<td className="px-2 py-1.5 tabular-nums">{g.n}</td>
							<td className="px-2 py-1.5 tabular-nums">{pct(g.pass_rate)}</td>
							{metrics.map((m) => (
								<td key={m} className="px-2 py-1.5 tabular-nums">
									{pct(g.means[m])}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}
