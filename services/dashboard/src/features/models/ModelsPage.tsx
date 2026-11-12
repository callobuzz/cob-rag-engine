import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { useId } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { modelsStrings as t } from "@/lib/strings/admin";
import { cn } from "@/lib/utils";
import { catalogQuery, MODEL_CATEGORIES, type ModelCategory } from "./api";
import { CatalogTable, categoryLabel } from "./CatalogTable";
import { EmbeddingCatalog } from "./EmbeddingCatalog";
import { OpenRouterDiscover } from "./OpenRouterDiscover";
import { PresetsTable } from "./PresetsTable";
import { MODEL_TABS, type ModelsSearch, type ModelTab } from "./search";

const route = getRouteApi("/_app/models");

/** `/models?tab=&category=`: answer models, presets, embedding models and OpenRouter discovery. */
export function ModelsPage() {
	const search = route.useSearch();
	const { tab = "answer", category } = search;
	const navigate = useNavigate();
	const setSearch = (next: Partial<ModelsSearch>) =>
		void navigate({ to: "/models", search: { ...search, ...next }, replace: true });
	const panels: Record<ModelTab, React.ReactNode> = {
		answer: <AnswerModels category={category} onCategory={(c) => setSearch({ category: c })} />,
		presets: <PresetsTable />,
		embeddings: <EmbeddingCatalog />,
		discover: <OpenRouterDiscover />,
	};
	return (
		<>
			<PageHeader title={t.page.title} description={t.page.lead} />
			<Tabs
				value={tab}
				onValueChange={(v) => setSearch({ tab: v === "answer" ? undefined : (v as ModelTab) })}
				className="min-w-0 gap-4"
			>
				<TabsList
					variant="line"
					aria-label={t.page.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0 [scrollbar-width:none]"
				>
					{MODEL_TABS.map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className={cn(
								"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
								"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
							)}
						>
							{t.page.tabs[k]}
						</TabsTrigger>
					))}
				</TabsList>
				{MODEL_TABS.map((k) => (
					<TabsContent key={k} value={k} className="min-w-0">
						{tab === k ? (
							<div className="overflow-x-auto rounded-lg border border-border bg-card">{panels[k]}</div>
						) : null}
					</TabsContent>
				))}
			</Tabs>
		</>
	);
}

function AnswerModels({
	category,
	onCategory,
}: {
	category: ModelCategory | undefined;
	onCategory: (c: ModelCategory | undefined) => void;
}) {
	const id = useId();
	const q = useQuery(catalogQuery(category));
	return (
		<>
			<div className="flex flex-wrap items-end justify-between gap-3 px-4 pt-3.5">
				<div className="grid w-[240px] max-w-full gap-1">
					<label htmlFor={id} className="text-[12.5px] font-semibold text-foreground-2">
						{t.catalog.category}
					</label>
					<NativeSelect
						id={id}
						value={category ?? ""}
						onChange={(e) => onCategory((e.target.value || undefined) as ModelCategory | undefined)}
					>
						<option value="">{t.catalog.allCategories}</option>
						{MODEL_CATEGORIES.map((c) => (
							<option key={c} value={c}>
								{categoryLabel(c)}
							</option>
						))}
					</NativeSelect>
				</div>
				<p className="m-0 max-w-[520px] text-[12.5px] text-muted-foreground">{t.catalog.estimate}</p>
			</div>
			{q.isPending ? (
				<TableSkeleton rows={5} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />
				</div>
			) : (
				<CatalogTable models={q.data.models} caption={t.catalog.caption} />
			)}
		</>
	);
}
