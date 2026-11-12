import { CircleAlert } from "lucide-react";
import { formatNumber } from "@/lib/format/number";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import type { BuildResult } from "./records";

const MAX_ERRORS_SHOWN = 50;

/** Step 3: the first 5 records exactly as they will be sent, counts, and rows with problems. */
export function RecordsPreview({ built, batchSize }: { built: BuildResult; batchSize: number }) {
	const n = built.records.length;
	const b = Math.ceil(n / batchSize);
	const errorRows = new Set(built.errors.map((e) => e.row)).size;
	return (
		<div className="grid gap-4">
			<p className="m-0 text-[13.5px] font-semibold" data-testid="records-count">
				{b <= 1
					? fill(t.records.countOne, { n: formatNumber(n) })
					: fill(t.records.count, { n: formatNumber(n), b: formatNumber(b), size: formatNumber(batchSize) })}
			</p>
			{n > 0 ? (
				<div className="overflow-x-auto rounded-lg border border-border">
					<table className="stack-sm w-full border-collapse text-[13px]">
						<caption className="border-b border-border px-3 py-2 text-left text-[12.5px] font-semibold text-foreground-2">
							{t.records.previewCaption}
						</caption>
						<thead>
							<tr>
								{[t.records.colId, t.records.colTitle, t.records.colText, t.records.colMetadata].map((h) => (
									<th
										key={h}
										scope="col"
										className="density-cell border-b border-border text-left font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase"
									>
										{h}
									</th>
								))}
							</tr>
						</thead>
						<tbody>
							{built.records.slice(0, 5).map((r) => (
								<tr key={r.id} className="border-b border-border align-top last:border-b-0">
									<td className="density-cell font-mono">{r.id}</td>
									<td className="density-cell">{r.title ?? "—"}</td>
									<td className="density-cell">
										<span className="line-clamp-3 max-w-[460px] break-words">{r.text}</span>
									</td>
									<td className="density-cell font-mono text-[12px]">
										{Object.keys(r.metadata ?? {}).length
											? Object.entries(r.metadata ?? {}).map(([k, v]) => (
													<span key={k} className="block">
														{k}: {v}
													</span>
												))
											: "—"}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			) : null}
			{built.errors.length ? (
				<div className="grid gap-2 rounded-lg border border-[color-mix(in_srgb,var(--destructive)_40%,var(--border))] p-3">
					<p role="alert" className="m-0 flex items-center gap-1.5 text-[13px] font-semibold text-destructive">
						<CircleAlert className="size-4" aria-hidden />
						{fill(t.records.errors, { n: formatNumber(errorRows) })}
					</p>
					<table className="w-full border-collapse text-[13px]">
						<caption className="sr-only">{t.records.errorsCaption}</caption>
						<thead>
							<tr>
								<th scope="col" className="w-20 py-1 text-left font-mono text-[10.5px] text-muted-foreground uppercase">
									{t.records.colRow}
								</th>
								<th scope="col" className="py-1 text-left font-mono text-[10.5px] text-muted-foreground uppercase">
									{t.records.colProblem}
								</th>
							</tr>
						</thead>
						<tbody>
							{built.errors.slice(0, MAX_ERRORS_SHOWN).map((e) => (
								<tr key={`${e.row}-${e.message}`}>
									<td className="py-1 font-mono">{formatNumber(e.row)}</td>
									<td className="py-1">{e.message}</td>
								</tr>
							))}
						</tbody>
					</table>
					{built.errors.length > MAX_ERRORS_SHOWN ? (
						<p className="m-0 text-[12.5px] text-muted-foreground">
							{fill(t.records.errorsMore, { n: formatNumber(built.errors.length - MAX_ERRORS_SHOWN) })}
						</p>
					) : null}
				</div>
			) : null}
		</div>
	);
}
