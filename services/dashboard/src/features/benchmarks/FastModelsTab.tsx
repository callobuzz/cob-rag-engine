import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useMemo } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { Badge } from "@/components/ui/badge";
import { type Currency, useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import { type FastModelBenchmarks, type FastModelResult, fastTags, pct, perThousand, seconds } from "./api";
import { barNames, TagBadges, ThinkingNote } from "./parts";
import { CurrencyToggle, Meter, MethodSection, MethodSheet, ModelName } from "./widgets";

function columns(data: FastModelBenchmarks, currency: Currency, open: ReadonlySet<string>): Column<FastModelResult>[] {
	const lang = (m: FastModelResult, k: string) => pct(m.per_language[k]);
	return [
		{
			id: "model",
			header: t.page.cols.model,
			rowHeader: true,
			sortValue: (m) => m.key,
			cell: (m) => (
				<span className="grid gap-1.5">
					<ModelName id={m.model} hosts={m.provider_order} />
					<span className="flex flex-wrap items-center gap-1">
						<TagBadges tags={fastTags(data, m)} selfHostable={open.has(m.model)} />
					</span>
					<ThinkingNote model={m} />
				</span>
			),
		},
		{
			id: "overall",
			header: t.page.cols.overall,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.overall,
			cell: (m) => <Meter value={m.overall} text={pct(m.overall)} />,
		},
		{ id: "en", header: t.page.cols.en, align: "right", stackLabel: true, cell: (m) => lang(m, "en") },
		{ id: "hi", header: t.page.cols.hi, align: "right", stackLabel: true, cell: (m) => lang(m, "hi") },
		{ id: "hiLatn", header: t.page.cols.hiLatn, align: "right", stackLabel: true, cell: (m) => lang(m, "hi-Latn") },
		{
			id: "turned",
			header: t.fast.turnedAway,
			align: "right",
			stackLabel: true,
			sortValue: (m) => 1 - m.business_recall,
			cell: (m) => (
				<span className="inline-grid justify-items-end gap-1 max-[600px]:justify-items-start">
					<span className={m.bars.business_recall ? undefined : "font-semibold text-warning"}>
						{pct(1 - m.business_recall)}
					</span>
					{m.bars.business_recall ? null : (
						<Badge variant="warn">
							<AlertTriangle aria-hidden />
							{t.fast.turnedAwayWarn}
						</Badge>
					)}
				</span>
			),
		},
		{
			id: "reply",
			header: t.page.cols.reply,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.reply_language,
			cell: (m) => pct(m.reply_language),
		},
		{
			id: "fallbacks",
			header: t.page.cols.fallbacks,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.fallback_rate,
			cell: (m) => pct(m.fallback_rate),
		},
		{ id: "p50", header: t.page.cols.p50, align: "right", stackLabel: true, cell: (m) => seconds(m.latency_s.p50) },
		{
			id: "p90",
			header: t.page.cols.p90,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.latency_s.p90 ?? Number.POSITIVE_INFINITY,
			cell: (m) => seconds(m.latency_s.p90),
		},
		{ id: "p99", header: t.page.cols.p99, align: "right", stackLabel: true, cell: (m) => seconds(m.latency_s.p99) },
		{
			id: "cost",
			header: t.page.cols.cost,
			align: "right",
			stackLabel: true,
			sortValue: (m) => m.usd_per_1000_labels ?? Number.POSITIVE_INFINITY,
			cell: (m) => perThousand(m.usd_per_1000_labels, currency),
		},
		{
			id: "result",
			header: t.page.cols.result,
			stackLabel: true,
			sortValue: (m) => (m.passes ? 0 : 1),
			cell: (m) =>
				m.passes ? (
					<Badge variant="ok">
						<CheckCircle2 aria-hidden />
						{t.page.passes}
					</Badge>
				) : (
					<span className="grid gap-1">
						<Badge variant="warn" className="h-auto min-h-[22px] py-0.5 whitespace-normal">
							<AlertTriangle aria-hidden />
							{t.belowBar}
						</Badge>
						<small className="max-w-[230px] text-muted-foreground">{fill(t.page.fails, { bars: barNames(m) })}</small>
					</span>
				),
		},
	];
}

/** The fast-model table with every bar; models below the quality bar are dimmed with the reason. */
export function FastModelsTab({ data, selfHostable }: { data: FastModelBenchmarks; selfHostable: readonly string[] }) {
	const currency = useCurrency();
	const open = useMemo(() => new Set(selfHostable), [selfHostable]);
	const cols = useMemo(() => columns(data, currency, open), [data, currency, open]);
	return (
		<section aria-label={t.fast.title} className="grid gap-3">
			<div className="flex flex-wrap items-end justify-between gap-3">
				<p className="m-0 max-w-[760px] text-[13px] text-foreground-2">{t.fast.lead}</p>
				<div className="flex flex-wrap items-center gap-2">
					<CurrencyToggle value={currency} />
					<FastMethod data={data} />
				</div>
			</div>
			<section
				// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable table region must be reachable by keyboard
				tabIndex={0}
				aria-label={fill(t.tableRegion, { name: t.fast.title })}
				className="overflow-x-auto rounded-lg border border-border bg-card focus-visible:outline-2 focus-visible:outline-ring"
			>
				<DataTable
					caption={t.page.fastCaption}
					columns={cols}
					rows={data.models}
					rowKey={(m) => m.key}
					initialSort={{ id: "overall", dir: "desc" }}
					rowClassName={(m) => (m.passes ? undefined : "bg-muted/70 text-foreground-2")}
				/>
			</section>
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.fast.turnedAwayHint}</p>
		</section>
	);
}

function FastMethod({ data }: { data: FastModelBenchmarks }) {
	const b = data.bars;
	const bars: [string, number][] = [
		["overall", b.overall],
		["per_language", b.per_language],
		["business_recall", b.business_recall],
		["reply_language", b.reply_language],
		["max_fallback_rate", b.max_fallback_rate],
	];
	return (
		<MethodSheet
			title={t.method.title}
			description={fill(t.method.date, { date: data.updated })}
			link={data.method.link}
		>
			<MethodSection title={t.method.tested}>
				<p className="m-0">{data.method.summary}</p>
			</MethodSection>
			<MethodSection title={t.method.bars}>
				<ul className="m-0 grid list-disc gap-0.5 pl-5">
					{bars.map(([k, v]) => (
						<li key={k}>{fill(t.page.barLines[k] ?? k, { v: pct(v) })}</li>
					))}
				</ul>
			</MethodSection>
			<MethodSection title={t.method.when}>
				<p className="m-0">{fill(t.method.date, { date: data.updated })}</p>
			</MethodSection>
		</MethodSheet>
	);
}
