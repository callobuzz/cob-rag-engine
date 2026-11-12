import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { PageHeader } from "@/components/shell/PageHeader";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CobStatusCard } from "@/features/engine/CobStatusCard";
import { cobAdminStrings as t } from "@/lib/strings/cob-admin";
import { cobDocsQuery } from "./api";
import { BuiltinDocs } from "./BuiltinDocs";
import { PlatformDocs } from "./PlatformDocs";

type Tab = "builtin" | "platform";

/** `/admin/cob` (COB9, operator only): the engine's built-in COB docs and the operator's own pages. */
export function CobAdminPage() {
	const [tab, setTab] = useState<Tab>("builtin");
	const docs = useQuery(cobDocsQuery);
	return (
		<div className="grid gap-4">
			<PageHeader title={t.page.title} description={t.page.lead} />
			<Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="min-w-0 gap-4">
				<TabsList
					variant="line"
					aria-label={t.page.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0"
				>
					{(["builtin", "platform"] as const).map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className="h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!"
						>
							{t.tabs[k]}
						</TabsTrigger>
					))}
				</TabsList>
				<TabsContent value="builtin" className="grid min-w-0 gap-4">
					<CobStatusCard />
					<BuiltinDocs query={docs} />
				</TabsContent>
				<TabsContent value="platform" className="min-w-0">
					<PlatformDocs query={docs} />
				</TabsContent>
			</Tabs>
		</div>
	);
}
