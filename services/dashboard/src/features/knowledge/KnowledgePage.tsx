import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Database } from "lucide-react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { principalKey } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { rememberKb } from "@/lib/kb-selection";
import { useMainKb } from "@/lib/main-kb";
import { KNOWLEDGE_TABS, type KnowledgeTab } from "@/lib/search";
import { knowledgeStrings as t } from "@/lib/strings/knowledge";
import { cn } from "@/lib/utils";
import { ArticlesTab } from "./ArticlesTab";
import { LastReadLine } from "./LastReadLine"; // TV5
import { WebsiteTabSlot } from "./WebsiteTabSlot";

const route = getRouteApi("/_app/knowledge");

/**
 * `/knowledge?tab=website|articles` for a business (BIZ3): what the assistant knows, on the
 * business's main knowledge base. A switcher appears only when there is more than one.
 */
export function KnowledgePage() {
	const { tab = "website" } = route.useSearch();
	const navigate = useNavigate();
	const principal = usePrincipal();
	const main = useMainKb();
	const header = <PageHeader title={t.title} description={t.lead} />;

	if (main.isLoading) {
		return (
			<div className="grid gap-4" aria-busy="true">
				{header}
				<CardSkeleton lines={4} />
			</div>
		);
	}
	if (main.isError) {
		return (
			<div className="grid gap-4">
				{header}
				<ErrorState error={main.error} title={t.loadError} onRetry={main.refetch} />
			</div>
		);
	}
	if (!main.kbId || !main.kb) {
		return (
			<div className="grid gap-4">
				{header}
				<EmptyState
					title={t.noKbTitle}
					body={t.noKbBody}
					primaryAction={
						<Link to="/kbs" search={{ new: 1 }} className={buttonVariants({ variant: "default" })}>
							<Database aria-hidden />
							{t.noKbAction}
						</Link>
					}
				/>
			</div>
		);
	}
	const kbId = main.kbId;
	return (
		<div className="grid gap-4">
			<PageHeader
				title={t.title}
				description={t.lead}
				actions={
					main.kbs.length > 1 ? (
						<div className="flex items-center gap-2">
							<label htmlFor="knowledge-kb" className="text-[13px] font-semibold">
								{t.kbLabel}
							</label>
							<NativeSelect
								id="knowledge-kb"
								value={kbId}
								onChange={(e) => {
									if (principal) rememberKb(principalKey(principal), e.target.value);
								}}
							>
								{main.kbs.map((k) => (
									<option key={k.id} value={k.id}>
										{k.name}
									</option>
								))}
							</NativeSelect>
						</div>
					) : null
				}
			/>
			<LastReadLine kbId={kbId} /> {/* TV5 */}
			<Tabs
				value={tab}
				onValueChange={(v) => void navigate({ to: "/knowledge", search: { tab: v as KnowledgeTab } })}
				className="min-w-0 gap-4"
			>
				<TabsList
					variant="line"
					aria-label={t.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0"
				>
					{KNOWLEDGE_TABS.map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className={cn(
								"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
								"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
							)}
						>
							{t.tabs[k]}
						</TabsTrigger>
					))}
				</TabsList>
				<TabsContent value="website" className="min-w-0">
					{tab === "website" ? <WebsiteTabSlot kbId={kbId} /> : null}
				</TabsContent>
				<TabsContent value="articles" className="min-w-0">
					{tab === "articles" ? <ArticlesTab key={kbId} kb={kbId} /> : null}
				</TabsContent>
			</Tabs>
		</div>
	);
}
