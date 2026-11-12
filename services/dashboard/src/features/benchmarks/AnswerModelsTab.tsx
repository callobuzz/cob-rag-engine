import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useMemo, useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { Badge } from "@/components/ui/badge";
import { type Currency, useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import {
	type AnswerBenchmarks,
	type AnswerModelResult,
	hindiCautions,
	type LanguageFilter,
	measures,
	ms,
	pct0,
	perThousand,
} from "./api";
import { TagBadges } from "./parts";
import { CurrencyToggle, LanguageToggle, Meter, MethodSection, MethodSheet, ModelName } from "./widgets";

const HINDI_FILTERS: LanguageFilter[] = ["hi", "hi-Latn", "hindi_all"];

function Result({ m, bars, lang }: { m: AnswerModelResult; bars: AnswerBenchmarks["bars"]; lang: LanguageFilter }) {
	if (!m.passes) {
		return (
			<span className="grid gap-1">
				<Badge variant="warn" className="h-auto min-h-[22px] py-0.5 whitespace-normal">
					<AlertTriangle aria-hidden />
					{t.answer.below}
				</Badge>
				<small className="max-w-[230px] text-muted-foreground">
					{fill(t.answer.whyLine, { reasons: m.fails.map((f) => t.answer.why[f] ?? f).join(", ") })}
				</small>
			</span>
		);
	}
	const cautions = HINDI_FILTERS.includes(lang) ? hindiCautions(m, bars) : [];
	return (
		<span className="grid gap-1">
			<Badge variant="ok">
				<CheckCircle2 aria-hidden />
				{t.answer.meets}
			</Badge>
			{cautions.length > 0 ? (
				<small className="max-w-[230px] text-warning">
					{fill(t.answer.hindiCaution, {
						reasons: cautions
							.map((c) =>
								fill(t.answer.cautions[c] ?? c, {
									v: pct0(
										c === "pass"
											? bars.hindi_pass_min
											: c === "noAnswer"
												? bars.hindi_no_answer_min
												: bars.hindi_register_min,
									),
								}),
							)
							.join(", "),
					})}
				</small>
			) : null}
		</span>
	);
}

function columns(
	lang: LanguageFilter,
	currency: Currency,
	bars: AnswerBenchmarks["bars"],
): Column<AnswerModelResult>[] {
	const get = (m: AnswerModelResult) => measures(m, lang);
	const dash = (v: number | null, text: string) => (v == null ? t.answer.notGraded : text);
	return [
		{
			id: "model",
			header: t.answer.cols.model,
			rowHeader: true,
			sortValue: (m) => m.model,
			cell: (m) => (
				<span className="grid gap-1.5">
					<ModelName id={m.model} hosts={m.provider_order} />
					<span className="flex flex-wrap items-center gap-1">
						<TagBadges tags={m.tags} selfHostable={m.self_hostable} />
					</span>
					<small className="text-muted-foreground">
						{m.thinking === "off" ? t.answer.thinkingOff : fill(t.answer.thinkingAt, { v: m.thinking })}
					</small>
				</span>
			),
		},
		{
			id: "pass",
			header: t.answer.cols.pass,
			align: "right",
			stackLabel: true,
			sortValue: (m) => get(m).pass ?? -1,
			cell: (m) => <Meter value={get(m).pass} text={pct0(get(m).pass)} />,
		},
		{
			id: "noAnswer",
			header: t.answer.cols.noAnswer,
			align: "right",
			stackLabel: true,
			sortValue: (m) => get(m).noAnswer ?? -1,
			cell: (m) => {
				const v = get(m).noAnswer;
				const low = v != null && v < bars.no_answer_min;
				return <Meter value={v} text={pct0(v)} tone={low ? "warn" : "primary"} />;
			},
		},
		{
			id: "tables",
			header: t.answer.cols.tables,
			align: "right",
			stackLabel: true,
			sortValue: (m) => get(m).tables ?? -1,
			cell: (m) => <Meter value={get(m).tables} text={pct0(get(m).tables)} />,
		},
		{
			id: "ttft",
			header: t.answer.cols.ttft,
			align: "right",
			stackLabel: true,
			sortValue: (m) => get(m).ttftMs ?? Number.POSITIVE_INFINITY,
			cell: (m) => ms(get(m).ttftMs),
		},
		{
			id: "cost",
			header: t.answer.cols.cost,
			align: "right",
			stackLabel: true,
			sortValue: (m) => get(m).usd ?? Number.POSITIVE_INFINITY,
			cell: (m) => perThousand(get(m).usd, currency),
		},
		{
			id: "natural",
			header: t.answer.cols.natural,
			align: "right",
			stackLabel: true,
			sortValue: (m) => get(m).naturalness ?? -1,
			cell: (m) => dash(get(m).naturalness, `${get(m).naturalness?.toFixed(1)} / 5`),
		},
		{
			id: "register",
			header: t.answer.cols.register,
			align: "right",
			stackLabel: true,
			sortValue: (m) => get(m).register ?? -1,
			cell: (m) => dash(get(m).register, pct0(get(m).register)),
		},
		{
			id: "result",
			header: t.answer.cols.result,
			stackLabel: true,
			sortValue: (m) => (m.passes ? 0 : 1),
			cell: (m) => <Result m={m} bars={bars} lang={lang} />,
		},
	];
}

/** The answer-model table: a language filter re-reads every measure for that language. */
export function AnswerModelsTab({ data }: { data: AnswerBenchmarks }) {
	const currency = useCurrency();
	const [lang, setLang] = useState<LanguageFilter>("all");
	const cols = useMemo(() => columns(lang, currency, data.bars), [lang, currency, data.bars]);
	return (
		<section aria-label={t.answer.title} className="grid gap-3">
			<div className="flex flex-wrap items-end justify-between gap-3">
				<p className="m-0 max-w-[760px] text-[13px] text-foreground-2">{t.answer.lead}</p>
				<div className="flex flex-wrap items-center gap-2">
					<LanguageToggle value={lang} onChange={setLang} />
					<CurrencyToggle value={currency} />
					<AnswerMethod data={data} />
				</div>
			</div>
			<section
				// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable table region must be reachable by keyboard
				tabIndex={0}
				aria-label={fill(t.tableRegion, { name: t.answer.title })}
				className="overflow-x-auto rounded-lg border border-border bg-card focus-visible:outline-2 focus-visible:outline-ring"
			>
				<DataTable
					key={lang}
					caption={`${t.answer.caption}: ${t.toolbar.languages[lang]}`}
					columns={cols}
					rows={data.models}
					rowKey={(m) => m.key}
					initialSort={{ id: "pass", dir: "desc" }}
					rowClassName={(m) => (m.passes ? undefined : "bg-muted/70 text-foreground-2")}
				/>
			</section>
			<p className="m-0 text-[12.5px] text-muted-foreground">
				{lang === "en" ? t.answer.colHints.natural : t.answer.colHints.noAnswer}
			</p>
		</section>
	);
}

function AnswerMethod({ data }: { data: AnswerBenchmarks }) {
	const b = data.bars;
	const it = data.items;
	const money = (v: number) => `$${v.toFixed(2)}`;
	return (
		<MethodSheet
			title={t.method.title}
			description={fill(t.method.date, { date: data.updated })}
			link={data.method_link}
		>
			<MethodSection title={t.method.tested}>
				<p className="m-0">{data.method}</p>
				<p className="m-0">
					{fill(t.method.items, {
						total: it.total,
						en: it.by_language.en ?? 0,
						hi: it.by_language.hi ?? 0,
						hiLatn: it.by_language["hi-Latn"] ?? 0,
						unans: it.unanswerable,
						tables: it.tables,
					})}
				</p>
				<p className="m-0">{t.method.thinkingNote}</p>
			</MethodSection>
			<MethodSection title={t.method.bars}>
				<ul className="m-0 grid list-disc gap-0.5 pl-5">
					{Object.entries(b).map(([k, v]) => (
						<li key={k}>{fill(t.answer.barLines[k] ?? k, { v: pct0(v) })}</li>
					))}
				</ul>
			</MethodSection>
			<MethodSection title={t.method.rules}>
				<ul className="m-0 grid list-disc gap-0.5 pl-5">
					{t.method.rulesList.map((r) => (
						<li key={r}>{r}</li>
					))}
				</ul>
			</MethodSection>
			<MethodSection title={t.method.when}>
				<p className="m-0">{fill(t.method.date, { date: data.updated })}</p>
				<p className="m-0">
					{fill(t.method.cost, {
						total: money(data.spend.total_usd),
						answers: money(data.spend.answers_usd),
						judge: money(data.spend.judge_usd),
					})}
				</p>
			</MethodSection>
		</MethodSheet>
	);
}
