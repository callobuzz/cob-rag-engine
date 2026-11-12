import { formatDate } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { fairUseMeterStrings as t } from "@/lib/strings/pipelines";
import { cn } from "@/lib/utils";
import type { MetricUsage, OwnerMetric } from "./api";
import { LIMIT_DEFS, microToUsd } from "./limits";

type Level = MetricUsage["level"];

const BAR: Record<Level, string> = {
	ok: "bg-primary",
	near: "bg-warning",
	soft: "bg-destructive",
	hard: "bg-destructive",
};

/**
 * One limit as a meter: the label, a bar, "412 of 500" and its state in words (never colour
 * alone). With no amounts (an owner's AI usage) it reads "82% used".
 */
export function MetricMeter({
	label,
	used,
	limit,
	pct,
	level,
	format,
	audience,
	resetsAt,
}: {
	label: string;
	used: number | null;
	limit: number | null;
	pct: number;
	level: Level;
	format?: (n: number) => string;
	audience: "admin" | "owner";
	resetsAt?: string | null;
}) {
	const fmt = format ?? formatNumber;
	const text =
		used != null && limit != null
			? `${fill(t.of, { used: fmt(used), limit: fmt(limit) })} · ${pct}%`
			: fill(t.pct, { pct });
	const levelText = (audience === "owner" ? t.ownerLevels : t.levels)[level] ?? "";
	const meterProps = {
		role: "meter" as const,
		"aria-label": label,
		"aria-valuemin": 0,
		"aria-valuemax": 100,
		"aria-valuenow": Math.min(100, pct),
		"aria-valuetext": levelText ? `${text}. ${levelText}` : text,
	};
	return (
		<div className="grid gap-1.5" data-level={level}>
			<div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[13px]">
				<span className="font-semibold">{label}</span>
				<span className="font-mono text-[12.5px] text-foreground-2">{text}</span>
			</div>
			<div {...meterProps} className="h-1.5 overflow-hidden rounded-[3px] bg-secondary">
				<i className={cn("block h-full rounded-[3px]", BAR[level])} style={{ width: `${Math.min(100, pct)}%` }} />
			</div>
			{levelText || resetsAt ? (
				<small className="flex flex-wrap gap-x-3 text-[12px] text-muted-foreground">
					{levelText ? (
						<b className={cn("font-semibold", level === "ok" ? "" : "text-foreground")}>{levelText}</b>
					) : null}
					{resetsAt ? <span>{fill(t.resets, { when: formatDate(resetsAt) })}</span> : null}
				</small>
			) : null}
		</div>
	);
}

const unitOf = (metric: string) => LIMIT_DEFS.find((d) => d.metric === metric);

/** The deployer's meter for one usage row (spend in dollars, storage in MB). */
export function AdminMetricMeter({ row }: { row: MetricUsage }) {
	const def = unitOf(row.metric);
	const format =
		def?.unit === "usd"
			? (n: number) => `$${microToUsd(n)}`
			: def?.unit === "mb"
				? (n: number) => `${formatNumber(n)} MB`
				: undefined;
	return (
		<MetricMeter
			label={row.label.charAt(0).toUpperCase() + row.label.slice(1)}
			used={row.used}
			limit={row.limit}
			pct={row.pct}
			level={row.level}
			format={format}
			audience="admin"
			resetsAt={row.resets_at}
		/>
	);
}

/** The owner's meter: counts as counts, AI usage as a share only. */
export function OwnerMetricMeter({ row }: { row: OwnerMetric }) {
	const def = unitOf(row.metric);
	const format = def?.unit === "mb" ? (n: number) => `${formatNumber(n)} MB` : undefined;
	const amounts = def?.unit !== "usd";
	return (
		<MetricMeter
			label={row.label.charAt(0).toUpperCase() + row.label.slice(1)}
			used={amounts ? row.used : null}
			limit={amounts ? row.limit : null}
			pct={row.pct}
			level={row.level}
			format={format}
			audience="owner"
			resetsAt={row.resets_at}
		/>
	);
}
