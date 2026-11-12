import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { useCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import {
	type AnswerBenchmarks,
	type AnswerModelResult,
	answerBenchmarksQuery,
	type FastModelBenchmarks,
	type FastModelResult,
	fastBenchmarksQuery,
	fastBudget,
	fastTags,
	friendlyName,
	ms,
	pct,
	pct0,
	perThousand,
	savingPercent,
	seconds,
} from "./api";
import { TagBadges } from "./parts";

function Tile({
	label,
	id,
	hosts,
	why,
	badges,
}: {
	label: string;
	id: string | null;
	hosts?: readonly string[];
	why: ReactNode;
	badges?: ReactNode;
}) {
	return (
		<li className="grid content-start gap-2 rounded-lg border border-border bg-card/80 p-4">
			<span className="font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase">
				{label}
			</span>
			{id ? (
				<>
					<span className="grid gap-0.5">
						<strong className="font-display text-[20px] leading-tight font-bold tracking-[-.01em]">
							{friendlyName(id)}
						</strong>
						<code className="font-mono text-[11.5px] break-all text-muted-foreground">
							{id}
							{hosts && hosts.length > 0 ? ` @ ${hosts.join(", ")}` : ""}
						</code>
					</span>
					<p className="m-0 text-[13px] text-foreground-2">{why}</p>
					{badges ? <span className="flex flex-wrap gap-1">{badges}</span> : null}
				</>
			) : (
				<p className="m-0 text-[13px] text-muted-foreground">{t.hero.none}</p>
			)}
		</li>
	);
}

function pick(models: readonly AnswerModelResult[], key: string | null): AnswerModelResult | undefined {
	return key ? models.find((m) => m.key === key) : undefined;
}

/** "Recommended setup": the engine's picks, each with the numbers that earned it. */
export function RecommendedHero() {
	const answer = useQuery(answerBenchmarksQuery);
	const fast = useQuery(fastBenchmarksQuery);
	const currency = useCurrency();
	if (answer.isPending || fast.isPending) return <Skeleton className="h-44 w-full rounded-lg" />;
	const a: AnswerBenchmarks | null = answer.data?.benchmarks ?? null;
	const f: FastModelBenchmarks | null = fast.data?.benchmarks ?? null;
	if (!a && !f) return null;
	const eng = a ? pick(a.models, a.recommendations.default) : undefined;
	const hin = a ? pick(a.models, a.recommendations.hindi_default) : undefined;
	const fastRec: FastModelResult | undefined = f?.models.find((m) => m.key === f.recommendations.default);
	const budget = fastBudget(f);
	const hindiAll = hin?.by_language.hindi_all;
	return (
		<section
			aria-label={t.hero.title}
			className="grid gap-4 rounded-lg border border-border bg-[linear-gradient(135deg,var(--primary-soft),transparent_65%)] bg-card p-4 min-[861px]:p-5"
		>
			<div className="flex flex-wrap items-center gap-2">
				<Sparkles className="size-4 text-primary" aria-hidden />
				<h2 className="m-0 text-[17px] font-bold">{t.hero.title}</h2>
				<span className="text-[13px] text-muted-foreground">{t.hero.lead}</span>
			</div>
			<ul className="m-0 grid list-none gap-3 p-0 min-[700px]:grid-cols-2 min-[1200px]:grid-cols-4">
				<Tile
					label={t.hero.english}
					id={eng?.model ?? null}
					hosts={eng?.provider_order}
					why={
						eng
							? fill(t.hero.englishWhy, {
									pass: pct0(eng.passed),
									declined: pct0(eng.refusal_correct_unanswerable),
									cost: perThousand(eng.usd_per_1000_answers, currency),
								})
							: null
					}
					badges={eng ? <TagBadges tags={eng.tags} selfHostable={eng.self_hostable} /> : null}
				/>
				<Tile
					label={t.hero.hindi}
					id={hin?.model ?? null}
					hosts={hin?.provider_order}
					why={
						hin && hindiAll
							? fill(t.hero.hindiWhy, {
									pass: pct0(hindiAll.passed),
									declined: pct0(hindiAll.refusal_correct_unanswerable),
									register: pct0(hindiAll.register_ok),
									ttft: ms(hindiAll.ttft_p50_ms),
								})
							: null
					}
					badges={hin ? <TagBadges tags={hin.tags} selfHostable={hin.self_hostable} /> : null}
				/>
				<Tile
					label={t.hero.fast}
					id={fastRec?.model ?? null}
					hosts={fastRec?.provider_order}
					why={
						fastRec
							? fill(t.hero.fastWhy, {
									p90: seconds(fastRec.latency_s.p90),
									acc: pct(fastRec.overall),
									cost: perThousand(fastRec.usd_per_1000_labels, currency),
								})
							: null
					}
					badges={
						fastRec ? (
							<TagBadges tags={fastTags(f, fastRec)} selfHostable={a?.self_hostable.includes(fastRec.model)} />
						) : null
					}
				/>
				{budget ? (
					<Tile
						label={t.hero.budget}
						id={budget.model}
						hosts={budget.provider_order}
						why={(() => {
							const save = savingPercent(fastRec?.usd_per_1000_labels, budget.usd_per_1000_labels);
							const values = {
								acc: pct(budget.overall),
								cost: perThousand(budget.usd_per_1000_labels, currency),
							};
							return save != null && fastRec
								? fill(t.hero.budgetWhy, { ...values, save, than: friendlyName(fastRec.model) })
								: fill(t.hero.budgetNoSaving, values);
						})()}
						badges={<TagBadges tags={fastTags(f, budget)} selfHostable={a?.self_hostable.includes(budget.model)} />}
					/>
				) : null}
			</ul>
		</section>
	);
}
