import { getRouteApi } from "@tanstack/react-router";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { Segmented } from "@/components/patterns/Segmented";
import { PageHeader } from "@/components/shell/PageHeader";
import { OutcomesTab } from "@/features/outcomes/OutcomesTab";
import type { Corpus } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { insightsStrings as t } from "@/lib/strings/insights";
import { ConfidenceTab } from "./ConfidenceTab";
import { FeedbackTab } from "./FeedbackTab";
import { GapsTab } from "./GapsTab";
import { MemoriesTab } from "./MemoriesTab";
import { QuestionsTab } from "./QuestionsTab";
import { INSIGHTS_TABS, type InsightsSearch, type InsightsTab } from "./search";
import { TrendsTab } from "./TrendsTab";
import { PageTabs } from "./ui";

const route = getRouteApi("/_app/insights");

/**
 * `/insights` (D9): outcomes, questions, trends, confidence, knowledge gaps, feedback and memories for the
 * selected knowledge base, or — `?scope=all` — for all of the caller's knowledge bases
 * (`corpus_id` omitted: a tenant gets its own, the super admin everything). Memories work on
 * one knowledge base only.
 */
export function InsightsPage() {
	return (
		<KbScopedPage title={t.title} emptyBody={t.emptyKbBody}>
			{(kb) => <InsightsBody kb={kb} />}
		</KbScopedPage>
	);
}

export interface ScopeProps {
	kb: Corpus;
	/** undefined = all knowledge bases in the caller's scope. */
	corpusId: string | undefined;
	search: InsightsSearch;
	setSearch: (patch: Partial<InsightsSearch>) => void;
}

function InsightsBody({ kb }: { kb: Corpus }) {
	const search = route.useSearch();
	const navigate = route.useNavigate();
	const principal = usePrincipal();
	const tab = search.tab ?? "outcomes";
	const all = search.scope === "all";
	const setSearch = (patch: Partial<InsightsSearch>) => void navigate({ search: (prev) => ({ ...prev, ...patch }) });
	const props: ScopeProps = { kb, corpusId: all ? undefined : kb.id, search, setSearch };

	return (
		<>
			<PageHeader
				title={t.title}
				description={t.lead}
				actions={
					<Segmented
						label={t.scope.label}
						value={all ? "all" : "kb"}
						options={[
							{ value: "kb", label: kb.name },
							{ value: "all", label: principal?.view === "admin" ? t.scope.allAdmin : t.scope.all },
						]}
						onChange={(v) =>
							setSearch({
								scope: v === "all" ? "all" : undefined,
								page: undefined,
								report: undefined,
								question: undefined,
							})
						}
					/>
				}
			/>
			<PageTabs<InsightsTab>
				label={t.tabsLabel}
				tabs={INSIGHTS_TABS}
				value={tab}
				labels={t.tabs}
				onChange={(v) => setSearch({ tab: v, page: undefined, report: undefined, question: undefined })}
				panels={{
					outcomes: () => <OutcomesTab {...props} />,
					questions: () => <QuestionsTab {...props} />,
					trends: () => <TrendsTab {...props} />,
					confidence: () => <ConfidenceTab {...props} />,
					gaps: () => <GapsTab {...props} />,
					feedback: () => <FeedbackTab {...props} />,
					memories: () => <MemoriesTab {...props} />,
				}}
			/>
		</>
	);
}
