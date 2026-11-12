import { Server } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import { type FastModelResult, failedBars } from "./api";

type Variant = "info" | "ok" | "warn" | "outline";
const TAG_VARIANT: Record<string, Variant> = {
	recommended: "info",
	hindi_recommended: "info",
	budget: "ok",
	hindi_budget: "ok",
	fastest: "ok",
	cheapest: "ok",
	most_accurate: "ok",
	best_hindi_tone: "outline",
};
/** Badges are shown in this order whatever order the engine lists the tags in. */
const TAG_ORDER = [
	"recommended",
	"hindi_recommended",
	"budget",
	"hindi_budget",
	"fastest",
	"cheapest",
	"most_accurate",
	"best_hindi_tone",
];

/** One badge per tag the engine computed (any tag it adds later still shows, in its own words). */
export function TagBadges({ tags, selfHostable = false }: { tags: readonly string[]; selfHostable?: boolean }) {
	const ordered = [...tags].sort((a, b) => {
		const ia = TAG_ORDER.indexOf(a);
		const ib = TAG_ORDER.indexOf(b);
		return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
	});
	return (
		<>
			{ordered.map((k) => (
				<Badge key={k} variant={TAG_VARIANT[k] ?? "outline"} title={t.tagHints[k]}>
					{t.tags[k] ?? k}
				</Badge>
			))}
			{selfHostable ? (
				<Badge variant="outline" title={t.tagHints.self_hostable}>
					<Server aria-hidden />
					{t.tags.self_hostable}
				</Badge>
			) : null}
		</>
	);
}

export function barNames(m: FastModelResult): string {
	return failedBars(m)
		.map((k) => t.bars[k])
		.join(", ");
}

/** "Below the quality bar" with the bars that failed; nothing for a passing row. */
export function BelowBar({ model }: { model: FastModelResult }) {
	if (model.passes) return null;
	return (
		<p role="note" className="m-0 flex flex-wrap items-center gap-1.5 text-[12.5px] text-warning">
			<Badge variant="warn">{t.belowBar}</Badge>
			<span>{fill(t.belowBarWhy, { bars: barNames(model) })}</span>
		</p>
	);
}

export function ThinkingNote({ model }: { model: FastModelResult }) {
	return model.thinking_control_verified ? null : (
		<small className="text-muted-foreground">{t.thinkingUnverified}</small>
	);
}
