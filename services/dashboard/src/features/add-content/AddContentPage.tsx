import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { FileText, Upload } from "lucide-react";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { useAnnouncer } from "@/components/patterns/LiveRegion";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Panel } from "@/features/kbs/ui";
import { type Corpus, infoQuery } from "@/lib/api/queries";
import { ADD_TABS, type AddTab, type JobStatus } from "@/lib/search";
import { contentStrings as t } from "@/lib/strings/content";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { checkFile, uploadLimits } from "./api";
import { JobsTable } from "./JobsTable";
import { RecordsImport } from "./RecordsImport";
import { UploadDropzone } from "./UploadDropzone";
import { UploadRow } from "./UploadRow";
import { isFinishedRow, optionProblems, uploadQueue, useUploadItems } from "./upload-queue";

const route = getRouteApi("/_app/add");

/** `/add?kb=&tab=upload|records|jobs&status=&page=` (mockup "Add content"). */
export function AddContentPage() {
	return <KbScopedPage title={t.add.title}>{(kb) => <AddContent key={kb.id} kb={kb} />}</KbScopedPage>;
}

function AddContent({ kb }: { kb: Corpus }) {
	const { tab = "upload", status, page = 1 } = route.useSearch();
	const navigate = useNavigate();
	const info = useQuery(infoQuery);
	const setSearch = (next: { tab?: AddTab; status?: JobStatus; page?: number }) =>
		void navigate({ to: "/add", search: { kb: kb.id, tab, status, page: page > 1 ? page : undefined, ...next } });

	return (
		<>
			<PageHeader
				title={t.add.title}
				description={fill(t.add.lead, { kb: kb.name })}
				actions={
					<Link to="/documents" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
						<FileText aria-hidden />
						{t.documents.title}
					</Link>
				}
			/>
			<Tabs
				value={tab}
				onValueChange={(v) => setSearch({ tab: v as AddTab, status: undefined, page: undefined })}
				className="min-w-0 gap-4"
			>
				<TabsList
					variant="line"
					aria-label={t.add.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0"
				>
					{ADD_TABS.map((k) => (
						<TabsTrigger
							key={k}
							value={k}
							className={cn(
								"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
								"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
							)}
						>
							{t.add.tabs[k]}
						</TabsTrigger>
					))}
				</TabsList>
				<TabsContent value="upload" className="min-w-0">
					{tab === "upload" ? <UploadTab kb={kb.id} limits={info.data ? uploadLimits(info.data) : null} /> : null}
				</TabsContent>
				<TabsContent value="records" className="min-w-0">
					{tab === "records" ? <RecordsImport kb={kb.id} info={info.data} /> : null}
				</TabsContent>
				<TabsContent value="jobs" className="min-w-0">
					{tab === "jobs" ? (
						<JobsTable kb={kb.id} status={status} page={page} onFilter={(n) => setSearch({ tab: "jobs", ...n })} />
					) : null}
				</TabsContent>
			</Tabs>
		</>
	);
}

// BIZ3: exported for the Knowledge page (Articles & files)
export function UploadTab({ kb, limits }: { kb: string; limits: ReturnType<typeof uploadLimits> | null }) {
	const items = useUploadItems(kb);
	const [announce, liveRegion] = useAnnouncer();
	const ready = items.filter((it) => it.phase === "ready");
	const startable = ready.filter((it) => Object.keys(optionProblems(it.options)).length === 0);
	const finished = items.some(isFinishedRow);

	return (
		<div className="grid gap-4">
			<UploadDropzone
				limits={limits}
				onFiles={(files) =>
					uploadQueue.add(
						kb,
						files,
						files.map((f) => (limits ? checkFile(f, limits) : null)),
					)
				}
			/>
			{items.length ? (
				<Panel
					title={t.upload.list}
					aside={
						<div className="flex flex-wrap gap-2">
							{finished ? (
								<Button size="sm" variant="outline" onClick={() => uploadQueue.clearFinished(kb)}>
									{t.upload.clearFinished}
								</Button>
							) : null}
							{ready.length ? (
								<Button
									size="sm"
									variant="default"
									disabled={startable.length === 0}
									onClick={() =>
										uploadQueue.start(
											kb,
											startable.map((it) => it.id),
										)
									}
								>
									<Upload aria-hidden />
									{startable.length === 1 ? t.upload.startOne : fill(t.upload.start, { n: startable.length })}
								</Button>
							) : null}
						</div>
					}
				>
					<ul className="m-0 grid list-none p-0" aria-label={t.upload.list}>
						{items.map((it) => (
							<UploadRow key={it.id} item={it} announce={announce} />
						))}
					</ul>
				</Panel>
			) : null}
			{liveRegion}
		</div>
	);
}
