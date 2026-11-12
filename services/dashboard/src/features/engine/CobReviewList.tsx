import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { CopyButton } from "@/components/patterns/CopyButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/features/kbs/ui";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatRelative } from "@/lib/format/date";
import { engineStrings } from "@/lib/strings/admin";

const t = engineStrings.cobReview;

export type CobReviewItem = components["schemas"]["CobReviewItem"];
type Rating = "all" | "down";

/** COB's weak answers (`GET /admin/cob/review`): operator only; the list the operator fixes docs from. */
export function cobReviewQuery(days: number, rating: Rating) {
	return {
		queryKey: ["admin", "cob", "review", days, rating] as const,
		queryFn: () => unwrap(api().GET("/api/v1/admin/cob/review", { params: { query: { days, rating, limit: 50 } } })),
	};
}

const DOCS_FILES: Record<string, string> = {
	guide: "docs/guide.html",
	deploy: "docs/deploy.html",
	benchmarks: "docs/benchmarks.html",
	readme: "README.md",
	changelog: "CHANGELOG.md",
	settings: "src/callobuzz_rag/core/config.py (the settings pages are generated from it)",
	api: "the route's docstring in src/callobuzz_rag/api/routes/ (the API pages are generated)",
};

/** The file in the repository a help page lives in, shown as text (COB's content is code-reviewed files). */
export function helpPageFile(slug: string | null): string | null {
	if (!slug) return null;
	const [source, stem] = slug.split("/");
	if (source === "screens" && stem) return `docs/cob/screens/${stem}.md`;
	return (source && DOCS_FILES[source]) || null;
}

/** One JSONL line for `docs/cob/eval/cob-eval.jsonl`, to be completed by hand before it is committed. */
export function evalLine(item: CobReviewItem): string {
	const role = item.role === "unknown" ? "owner" : item.role;
	const stem =
		item.group
			.replace(/[^a-z0-9]+/gi, "-")
			.replace(/^-|-$/g, "")
			.slice(0, 40) || "question";
	return JSON.stringify({
		id: `review-${stem}`,
		role,
		language: item.language,
		question: item.question,
		expect: {
			behaviour: "answer",
			intent: "docs",
			tool: null,
			slug: item.top_slug ?? null,
			anchor: null,
			link_route: null,
			must_not_include: [],
		},
		routing: "model",
		tags: ["review"],
	});
}

export function CobReviewList() {
	const [days, setDays] = useState("30");
	const [rating, setRating] = useState<Rating>("all");
	const q = useQuery(cobReviewQuery(Number(days), rating));
	const items = q.data?.items ?? [];

	return (
		<div id="cob-review">
			<Panel
				title={t.title}
				sub={t.sub(Number(days))}
				aside={
					<div className="flex flex-wrap items-center gap-2">
						<Segmented label={t.days} value={days} options={[...t.daysOptions]} onChange={setDays} />
						<Segmented
							label={t.filter}
							value={rating}
							options={[
								{ value: "all", label: t.filterAll },
								{ value: "down", label: t.filterDown },
							]}
							onChange={setRating}
						/>
					</div>
				}
			>
				{q.isPending ? (
					<CardSkeleton lines={4} />
				) : q.isError ? (
					<div className="p-3">
						<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
					</div>
				) : items.length === 0 ? (
					<div className="px-4 py-6 text-[13px]">
						<p className="m-0 font-semibold">{t.emptyTitle}</p>
						<p className="m-0 mt-1 text-muted-foreground">{t.emptyBody}</p>
					</div>
				) : (
					<ul className="m-0 grid list-none p-0">
						{items.map((item) => (
							<ReviewRow key={item.group} item={item} />
						))}
					</ul>
				)}
			</Panel>
		</div>
	);
}

function ReviewRow({ item }: { item: CobReviewItem }) {
	const file = helpPageFile(item.top_slug ?? null);
	return (
		<li className="grid gap-2 border-b border-border px-4 py-3 text-[13px] last:border-b-0">
			<div className="flex flex-wrap items-center gap-2">
				<b className="min-w-0 break-words">{item.question}</b>
				{item.reasons.map((r) => (
					<Badge key={r} variant={r === "thumbs_down" ? "bad" : "warn"}>
						{t.reasons[r] ?? r}
					</Badge>
				))}
			</div>
			<div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
				<span>{t.asked(item.count)}</span>
				<span>
					{t.roles[item.role] ?? item.role} · {t.languages[item.language] ?? item.language}
				</span>
				<span>
					{t.lastAt} {formatRelative(item.last_at)}
				</span>
				<span>
					{t.helpPage}: <code className="font-mono">{item.top_slug ?? t.helpPageNone}</code>
				</span>
			</div>
			{item.comments.length > 0 ? (
				<div>
					<span className="text-muted-foreground">{t.comments}</span>
					<ul className="m-0 mt-1 list-disc pl-5">
						{item.comments.map((c) => (
							<li key={c}>{c}</li>
						))}
					</ul>
				</div>
			) : null}
			<div className="flex flex-wrap items-center gap-3">
				{file ? (
					<span>
						{t.editPage}: <code className="font-mono">{file}</code>
					</span>
				) : null}
				<CopyButton value={evalLine(item)} label={t.addToSet} ariaLabel={`${t.addToSet}: ${item.question}`} />
			</div>
		</li>
	);
}
