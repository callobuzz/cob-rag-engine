import { Code2, FlaskConical, Globe, MessagesSquare, Radio } from "lucide-react";
import { SOURCE_ORDER, type SourceKind, totalsBySource } from "@/features/usage/source-labels";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";

const t = overviewStrings.sources;
const ICON = { widget: Globe, api: Code2, stream: Radio, conversation: MessagesSquare, eval: FlaskConical } as const;

/**
 * Mockup `.chart-sum`: questions per source for the whole range with their share. `GET /usage`
 * groups by one dimension only, so this is a totals row, not a per-day split (D2 non-goal). The
 * chart above is a single series, so sources are told apart by icon and name, not colour.
 */
export function SourceTotals({ rows }: { rows: { key: string | null; requests: number }[] }) {
	const by = totalsBySource(rows);
	const total = Object.values(by).reduce((a, n) => a + n, 0);
	return (
		<ul
			aria-label={t.label}
			className="m-0 grid list-none grid-cols-5 border-t border-border p-0 max-[860px]:grid-cols-2"
		>
			{SOURCE_ORDER.map((k: Exclude<SourceKind, "other">) => {
				const Icon = ICON[k];
				const n = by[k];
				return (
					<li
						key={k}
						data-source={k}
						className="grid gap-0.5 border-r border-border px-4 py-3 last:border-r-0 max-[860px]:nth-2:border-r-0 max-[860px]:nth-[-n+2]:border-b"
					>
						<span className="flex items-center gap-1.5 text-[12px] text-muted-foreground">
							<Icon className="size-3.5" aria-hidden />
							{t[k]}
						</span>
						<b className="font-display text-[18px] tabular-nums">{formatNumber(n)}</b>
						<small className="text-[12px] text-muted-foreground">
							{fill(t.share, { p: total ? formatPercent(n / total) : "—" })}
						</small>
					</li>
				);
			})}
		</ul>
	);
}
