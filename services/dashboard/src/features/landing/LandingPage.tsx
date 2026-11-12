import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { landingStrings as t } from "@/lib/strings/landing";
import { AddressCard } from "./AddressCard";
import { AskCard } from "./AskCard";
import { useLanding } from "./api";
import { DraftProvider, useDraft } from "./DraftProvider";
import type { TabKey } from "./draft";
import { PreviewPane } from "./PreviewPane";
import { PublishDialog } from "./PublishDialog";
import { StatusBar } from "./StatusBar";
import { TabBodies } from "./tabs";

const TAB_ORDER: TabKey[] = ["home", "look", "pricing", "pages", "menu", "languages", "versions"];

function Frame({ tab, setTab }: { tab: TabKey; setTab: (tab: TabKey) => void }) {
	const draft = useDraft();
	const [previewing, setPreviewing] = useState(false);
	const [publishing, setPublishing] = useState(false);
	return (
		<div className="grid grid-cols-[minmax(0,1fr)] gap-4">
			<StatusBar onPreview={() => setPreviewing(true)} onPublish={() => setPublishing(true)} />
			<PreviewPane open={previewing} onOpenChange={setPreviewing} />
			<PublishDialog open={publishing} onOpenChange={setPublishing} />
			{draft.state.warnings.length > 0 ? (
				<ul
					data-testid="landing-warnings"
					aria-label={t.status.warningsTitle}
					className="m-0 grid list-none gap-1 rounded-sm bg-secondary p-3 text-[13px] text-foreground-2"
				>
					{draft.state.warnings.map((w) => (
						<li key={w.code}>{w.message}</li>
					))}
				</ul>
			) : null}
			<div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 min-[1100px]:grid-cols-2">
				<AddressCard />
				<AskCard />
			</div>
			<Tabs value={tab} onValueChange={(v) => setTab(v as TabKey)} className="gap-4">
				<TabsList aria-label={t.tabsLabel} className="h-auto! w-full max-w-full justify-start overflow-x-auto">
					{TAB_ORDER.map((key) => (
						<TabsTrigger key={key} value={key} className="flex-none px-3 py-1.5">
							{t.tabs[key]}
						</TabsTrigger>
					))}
				</TabsList>
				{TAB_ORDER.map((key) => (
					<TabsContent key={key} value={key}>
						<TabBodies tab={key} />
					</TabsContent>
				))}
			</Tabs>
		</div>
	);
}

/**
 * `/admin/landing`: the operator builds and runs the public website from one page. Operators only
 * (the route refuses workspace views). Everything is edited in a local draft; "Save draft" stores
 * it, "Publish" makes it the live version and the switch shows it to visitors.
 */
export function LandingPage({ initialTab }: { initialTab?: TabKey }) {
	const q = useLanding();
	const [tab, setTab] = useState<TabKey>(initialTab ?? "home");
	return (
		<>
			<PageHeader title={t.title} description={t.lead} />
			{q.isPending ? (
				<CardSkeleton lines={8} />
			) : q.isError ? (
				<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
			) : (
				<DraftProvider state={q.data} onTab={setTab}>
					<Frame tab={tab} setTab={setTab} />
				</DraftProvider>
			)}
		</>
	);
}
