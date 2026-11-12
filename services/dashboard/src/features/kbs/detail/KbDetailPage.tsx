import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { MessageSquare, SearchX, Upload } from "lucide-react";
import type { ReactNode } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { NotAvailable } from "@/components/shell/NotAvailable";
import { NotIncluded } from "@/components/shell/NotIncluded"; // BIZ3
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AssistantTab } from "@/features/assistant/AssistantTab";
import { isApiError } from "@/lib/api/errors";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";
import { type Gate, useDashboardFeatures } from "@/lib/features"; // BIZ3
import { KB_TABS, type KbTab } from "@/lib/search";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { cn } from "@/lib/utils";
import { corpusQuery } from "../api";
import { KbAvatar } from "../KbAvatar";
import { ApiKeyTab } from "./ApiKeyTab";
import { DangerTab } from "./DangerTab";
import { EmbeddingTab } from "./EmbeddingTab";
import { ModelTab } from "./ModelTab";
import { OverviewTab } from "./OverviewTab";
import { RetrievalTab } from "./RetrievalTab";

const route = getRouteApi("/_app/kbs/$kbId");

// BIZ3: the plan gate behind each tab a deployer can hide from a business
const TAB_GATE: Partial<Record<KbTab, Gate>> = {
	model: "models",
	retrieval: "retrieval",
	embedding: "retrieval",
	key: "keys_agents",
};
// /BIZ3

/** `/kbs/$kbId` (mockup `[data-view=kb]`): header + seven tabs, `?tab=` in the URL. */
export function KbDetailPage() {
	const { kbId } = route.useParams();
	const { tab = "overview" } = route.useSearch();
	const navigate = useNavigate();
	const dashboard = useDashboardFeatures(); // BIZ3
	const q = useQuery(corpusQuery(kbId));

	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={2} />
				<CardSkeleton lines={5} />
			</div>
		);
	}
	if (q.isError) {
		if (isApiError(q.error) && q.error.status === 404) return <KbNotFound />;
		if (isApiError(q.error) && q.error.status === 403) return <NotAvailable />;
		return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
	}
	const kb = q.data;
	const meta = [kb.tenant_name, kb.description].filter(Boolean).join(" · ");

	// BIZ3: tabs the plan hides are not listed; a deep link to one says it is not included
	const tabs = KB_TABS.filter((k) => !TAB_GATE[k] || dashboard.shown(TAB_GATE[k] as Gate));
	const panels: Record<KbTab, ReactNode> = {
		overview: <OverviewTab kb={kb} />,
		model: <ModelTab kb={kb} />,
		assistant: <AssistantTab kb={kb} />,
		retrieval: <RetrievalTab kb={kb} />,
		embedding: <EmbeddingTab kb={kb} />,
		key: <ApiKeyTab kb={kb} />,
		danger: <DangerTab kb={kb} />,
	};

	return (
		<>
			<PageHeader
				title={kb.name}
				crumbs={[{ label: t.list.title, to: "/kbs" }, { label: kb.name }]}
				leading={<KbAvatar kb={kb} size={40} />}
				description={
					<>
						{meta} · <span className="font-mono text-[12.5px]">{kb.qdrant_collection}</span>
					</>
				}
				actions={
					<>
						<Link to="/playground" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
							<MessageSquare aria-hidden />
							{t.detail.openPlayground}
						</Link>
						<Link to="/add" search={{ kb: kb.id }} className={buttonVariants({ variant: "default" })}>
							<Upload aria-hidden />
							{t.detail.addContent}
						</Link>
					</>
				}
			/>
			<Tabs
				value={tab}
				onValueChange={(v) => void navigate({ to: ".", search: (prev) => ({ ...prev, tab: v as KbTab }) })}
				className="min-w-0 gap-4"
			>
				<TabsList
					variant="line"
					aria-label={t.detail.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0 [scrollbar-width:none]"
				>
					{tabs.map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className={cn(
								"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
								"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
								k === "danger" &&
									"text-destructive hover:text-destructive data-[state=active]:text-destructive dark:text-destructive dark:hover:text-destructive dark:data-[state=active]:text-destructive",
							)}
						>
							{t.detail.tabs[k]}
						</TabsTrigger>
					))}
				</TabsList>
				{KB_TABS.map((k) => (
					<TabsContent key={k} value={k} className="min-w-0">
						{tab === k ? tabs.includes(k) ? panels[k] : <NotIncluded /> : null}
					</TabsContent>
				))}
			</Tabs>
		</>
	);
}

function KbNotFound() {
	useDocumentTitle(t.detail.notFoundTitle);
	return (
		<section
			data-testid="kb-not-found"
			className="grid justify-items-start gap-3.5 rounded-lg border border-border bg-card p-8"
		>
			<span className="grid size-[52px] place-items-center rounded-[14px] bg-secondary text-muted-foreground">
				<SearchX className="size-[26px]" aria-hidden />
			</span>
			<h1 className="text-2xl font-bold" tabIndex={-1}>
				{t.detail.notFoundTitle}
			</h1>
			<p className="m-0 max-w-[560px] text-foreground-2">{t.detail.notFoundBody}</p>
			<Link to="/kbs" className={buttonVariants({ variant: "outline" })}>
				{t.detail.back}
			</Link>
		</section>
	);
}
