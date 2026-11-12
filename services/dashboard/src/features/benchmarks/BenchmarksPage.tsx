import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { AnswerModelsTab } from "./AnswerModelsTab";
import { answerBenchmarksQuery, fastBenchmarksQuery } from "./api";
import { FastModelsTab } from "./FastModelsTab";
import { RecommendedHero } from "./RecommendedHero";
import { RetrievalTab } from "./RetrievalTab";

type TabId = "answer" | "fast" | "retrieval";

/** `/benchmarks`: the recommended setup, then the answer models, fast models and retrieval results. */
export function BenchmarksPage() {
	const [tab, setTab] = useState<TabId>("answer");
	return (
		<div className="grid gap-4">
			<PageHeader title={t.page.title} description={t.page.lead} />
			<RecommendedHero />
			<Tabs value={tab} onValueChange={(v) => setTab(v as TabId)}>
				<TabsList variant="line" aria-label={t.tabs.label} className="max-w-full overflow-x-auto">
					<TabsTrigger value="answer">{t.tabs.answer}</TabsTrigger>
					<TabsTrigger value="fast">{t.tabs.fast}</TabsTrigger>
					<TabsTrigger value="retrieval">{t.tabs.retrieval}</TabsTrigger>
				</TabsList>
				<TabsContent value="answer" className="pt-2">
					<AnswerModels />
				</TabsContent>
				<TabsContent value="fast" className="pt-2">
					<FastModels />
				</TabsContent>
				<TabsContent value="retrieval" className="pt-2">
					<RetrievalTab />
				</TabsContent>
			</Tabs>
		</div>
	);
}

function AnswerModels() {
	const q = useQuery(answerBenchmarksQuery);
	if (q.isPending) return <CardSkeleton lines={6} />;
	if (q.isError) return <ErrorState error={q.error} title={t.answer.error} onRetry={() => void q.refetch()} />;
	if (!q.data.available || !q.data.benchmarks) {
		return <EmptyState title={t.page.emptyTitle} body={t.answer.empty} />;
	}
	return <AnswerModelsTab data={q.data.benchmarks} />;
}

function FastModels() {
	const q = useQuery(fastBenchmarksQuery);
	const answers = useQuery(answerBenchmarksQuery);
	if (q.isPending) return <CardSkeleton lines={6} />;
	if (q.isError) return <ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />;
	if (!q.data.available || !q.data.benchmarks) {
		return <EmptyState title={t.page.emptyTitle} body={t.page.emptyBody} />;
	}
	return <FastModelsTab data={q.data.benchmarks} selfHostable={answers.data?.benchmarks?.self_hostable ?? []} />;
}
