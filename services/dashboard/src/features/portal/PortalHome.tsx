import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { TabsContent } from "@/components/ui/tabs";
import type { Corpus } from "@/lib/api/queries";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { NewWorkspaceDialog } from "./NewWorkspaceDialog";
import { RulesPage } from "./RulesPage";
import { HOME_TABS, LineTabs, PortalGate, type PortalSearch } from "./ui";
import { WorkspaceList } from "./WorkspaceList";

const route = getRouteApi("/_app/portal/");

/** `/portal?kb=&tab=&status=&page=`: workspaces and routing rules of the selected knowledge base. */
export function PortalHome() {
	return (
		<PortalGate>
			<KbScopedPage title={t.home.title} emptyBody={t.list.emptyBody}>
				{(kb) => <Home key={kb.id} kb={kb} />}
			</KbScopedPage>
		</PortalGate>
	);
}

function Home({ kb }: { kb: Corpus }) {
	const { tab = "workspaces", status, page = 1 } = route.useSearch();
	const navigate = useNavigate();
	const [creating, setCreating] = useState(false);
	const setSearch = (next: Partial<PortalSearch>) =>
		void navigate({
			to: "/portal",
			search: {
				kb: kb.id,
				tab: tab === "workspaces" ? undefined : tab,
				status,
				page: page > 1 ? page : undefined,
				...next,
			},
			replace: true,
		});

	return (
		<div className="grid gap-5">
			<PageHeader
				title={t.home.title}
				description={fill(t.home.lead, { kb: kb.name })}
				actions={
					tab === "workspaces" ? (
						<Button variant="default" onClick={() => setCreating(true)}>
							<Plus aria-hidden />
							{t.home.newWorkspace}
						</Button>
					) : null
				}
			/>
			<LineTabs
				label={t.home.tabsLabel}
				value={tab}
				options={HOME_TABS.map((k) => ({ value: k, label: t.home.tabs[k] }))}
				onChange={(v) => setSearch({ tab: v === "workspaces" ? undefined : v, status: undefined, page: undefined })}
			>
				<TabsContent value="workspaces" className="min-w-0">
					{tab === "workspaces" ? (
						<WorkspaceList
							kb={kb}
							status={status}
							page={page}
							onStatus={(s) => setSearch({ status: s, page: undefined })}
							onPage={(p) => setSearch({ page: p > 1 ? p : undefined })}
							onCreate={() => setCreating(true)}
						/>
					) : null}
				</TabsContent>
				<TabsContent value="rules" className="min-w-0">
					{tab === "rules" ? <RulesPage kb={kb} /> : null}
				</TabsContent>
			</LineTabs>
			<NewWorkspaceDialog open={creating} onOpenChange={setCreating} kb={kb} />
		</div>
	);
}
