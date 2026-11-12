import { getRouteApi } from "@tanstack/react-router";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { PageHeader } from "@/components/shell/PageHeader";
import { QUALITY_TABS, type QualitySearch, type QualityTab } from "@/features/insights/search";
import { Note, PageTabs } from "@/features/insights/ui";
import { Panel } from "@/features/kbs/ui";
import type { Corpus } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { qualityStrings as t } from "@/lib/strings/insights";
import { AddonMissingCard, useEvalAddon } from "./addon";
import { CompareTab } from "./compare/CompareTab";
import { CriteriaTab } from "./criteria/CriteriaTab";
import { EvalRunDetail } from "./EvalRunDetail";
import { EvalRunsTab } from "./EvalRunsTab";
import { GoldenTab } from "./GoldenTab";
import { OptimizeRunDetail } from "./OptimizeRunDetail";
import { OptimizeTab } from "./OptimizeTab";
import { RunDetail } from "./runs/RunDetail";
import { RunsTab } from "./runs/RunsTab";

const route = getRouteApi("/_app/quality");

/**
 * `/quality`: golden sets of the selected knowledge base, test runs (quick checks and full
 * runs), comparisons, criteria and the judge model, for every account; the operator also sees
 * past traffic evaluations and optimisation runs (admin-only routes: a tenant sees a note and no
 * request is made).
 */
export function QualityPage() {
	return (
		<KbScopedPage title={t.title} emptyBody={t.emptyKbBody}>
			{(kb) => <QualityBody kb={kb} />}
		</KbScopedPage>
	);
}

function QualityBody({ kb }: { kb: Corpus }) {
	const search = route.useSearch();
	const navigate = route.useNavigate();
	const principal = usePrincipal();
	const { productName } = useBranding();
	const isAdmin = principal?.view === "admin";
	const addon = useEvalAddon();
	const tab = search.tab ?? "golden";
	const page = search.page ?? 1;
	const setSearch = (patch: Partial<QualitySearch>) => void navigate({ search: (prev) => ({ ...prev, ...patch }) });
	const open = (id: string) => setSearch({ run: id });
	const back = () => setSearch({ run: undefined });
	const onPage = (p: number) => setSearch({ page: p });
	const compareIds = (search.runs ?? "").split(",").filter(Boolean);

	const tenantNote = (title: string, note: string) => (
		<Panel title={title}>
			<Note testId="tenant-note">{fill(note, { product: productName })}</Note>
		</Panel>
	);

	return (
		<>
			<PageHeader title={t.title} description={t.lead} />
			{addon ? null : <AddonMissingCard />}
			<PageTabs<QualityTab>
				label={t.tabsLabel}
				tabs={QUALITY_TABS}
				value={tab}
				labels={t.tabs}
				onChange={(v) => setSearch({ tab: v, page: undefined, run: undefined, traffic: undefined })}
				panels={{
					golden: () => <GoldenTab kb={kb} page={page} onPage={onPage} />,
					runs: () =>
						search.run ? (
							<RunDetail kb={kb.id} id={search.run} onBack={back} />
						) : search.traffic ? (
							<EvalRunDetail id={search.traffic} onBack={() => setSearch({ traffic: undefined })} />
						) : (
							<>
								<RunsTab kb={kb.id} onOpen={open} />
								{isAdmin ? (
									<EvalRunsTab
										kb={kb}
										addon={addon}
										page={page}
										onPage={onPage}
										onOpen={(id) => setSearch({ traffic: id })}
									/>
								) : null}
							</>
						),
					compare: () => (
						<CompareTab kb={kb.id} ids={compareIds} onIds={(ids) => setSearch({ runs: ids.join(",") || undefined })} />
					),
					criteria: () => <CriteriaTab kb={kb.id} />,
					optimize: () =>
						!isAdmin ? (
							tenantNote(t.optimize.title, t.optimize.tenantNote)
						) : search.run ? (
							<OptimizeRunDetail id={search.run} fallbackKb={kb.id} onBack={back} />
						) : (
							<OptimizeTab kb={kb} addon={addon} page={page} onPage={onPage} onOpen={open} />
						),
				}}
			/>
		</>
	);
}
