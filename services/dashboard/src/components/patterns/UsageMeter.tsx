import { formatNumber } from "@/lib/format/number";
import { cn } from "@/lib/utils";

/** "7,412 of 10,000" (or "7,412 · unlimited"). */
export function usageText(used: number, limit: number | null): string {
	return limit == null ? `${formatNumber(used)} · unlimited` : `${formatNumber(used)} of ${formatNumber(limit)}`;
}

/** Share of the limit used (0–1, capped at 1); null when unlimited. */
export function usageShare(used: number, limit: number | null): number | null {
	if (limit == null || limit <= 0) return null;
	return Math.min(1, Math.max(0, used / limit));
}

/** Amber from 80 %, rose at 100 % (mockup `.progress.hot`); status also said in text. */
export function usageTone(used: number, limit: number | null): "ok" | "warn" | "full" {
	const share = usageShare(used, limit);
	if (share == null) return "ok";
	if (used >= (limit ?? Number.POSITIVE_INFINITY)) return "full";
	return share >= 0.8 ? "warn" : "ok";
}

function Bar({ used, limit, className }: { used: number; limit: number | null; className?: string }) {
	const share = usageShare(used, limit) ?? 0;
	const tone = usageTone(used, limit);
	return (
		<div className={cn("h-1.5 overflow-hidden rounded-[3px] bg-secondary", className)}>
			<i
				className={cn(
					"block h-full rounded-[3px]",
					tone === "full" ? "bg-destructive" : tone === "warn" ? "bg-warning" : "bg-primary",
				)}
				style={{ width: `${Math.round(share * 1000) / 10}%` }}
			/>
		</div>
	);
}

/**
 * A usage meter (mockup `.mm` in tables, `.usage-meter` on plan pages): `role="meter"` with the
 * value text "7,412 of 10,000"; an unlimited limit shows the count only (no meter role).
 */
export function UsageMeter({
	label,
	used,
	limit,
	hint,
	variant = "row",
}: {
	/** Accessible name, e.g. "Questions this month". */
	label: string;
	used: number;
	limit: number | null;
	hint?: string;
	/** `row`: label and value on top (plan pages); `cell`: bar then value (tables). */
	variant?: "row" | "cell";
}) {
	const text = usageText(used, limit);
	const tone = usageTone(used, limit);
	const status = tone === "full" ? "Limit reached" : tone === "warn" ? "Nearly full" : "";
	const toneText = status ? ` · ${status.toLowerCase()}` : "";
	const meterProps =
		limit == null
			? {}
			: {
					role: "meter" as const,
					"aria-label": label,
					"aria-valuemin": 0,
					"aria-valuemax": limit,
					"aria-valuenow": Math.min(used, limit),
					"aria-valuetext": `${text}${toneText}`,
				};
	if (variant === "cell") {
		return (
			<div className="grid min-w-[150px] gap-1" {...meterProps}>
				{limit == null ? null : <Bar used={used} limit={limit} />}
				<span
					className={cn(
						"text-[12px] tabular-nums text-foreground-2",
						tone === "full" && "font-semibold text-destructive",
						tone === "warn" && "font-semibold text-warning",
					)}
				>
					{text}
				</span>
			</div>
		);
	}
	return (
		<div className="grid gap-1.5 border-b border-border px-4 py-3.5 last:border-b-0">
			<div className="flex justify-between gap-3 text-[13.5px]">
				<span>{label}</span>
				<b className="font-mono font-medium tabular-nums">{text}</b>
			</div>
			{limit == null ? null : (
				<div {...meterProps}>
					<Bar used={used} limit={limit} className="h-2 rounded-[4px]" />
				</div>
			)}
			{hint || status ? (
				<small className={cn("text-[12px] text-muted-foreground", tone === "full" && "text-destructive")}>
					{[status, hint].filter(Boolean).join(" · ")}
				</small>
			) : null}
		</div>
	);
}
