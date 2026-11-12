import { useQuery } from "@tanstack/react-query";
import { ExternalLink, FileText } from "lucide-react";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { currentRagUrl } from "@/lib/auth/store";
import { formatDate } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { legalStrings as t } from "@/lib/strings/legal";
import { AcceptancesTable } from "./AcceptancesTable";
import { enginePageUrl, type LegalPageSummary, legalListQuery, legalSettingsQuery } from "./api";
import { LegalEditor } from "./LegalEditor";
import { LegalSettingsCard } from "./LegalSettingsCard";

/** What a card says about a page, most pressing first. */
export function pageStatus(
	p: Pick<LegalPageSummary, "published_version" | "published_at" | "draft_changed" | "company_changed">,
): string {
	if (p.published_version == null) return t.status.notPublished;
	if (p.company_changed) return t.status.companyChanged;
	const base = fill(t.status.version, { version: String(p.published_version), date: formatDate(p.published_at) });
	return p.draft_changed ? `${base} · ${t.status.draftChanges}` : base;
}

function PageCard({ page, onEdit }: { page: LegalPageSummary; onEdit: () => void }) {
	const live = page.published_version != null;
	return (
		<li data-testid={`legal-card-${page.slug}`} className="grid gap-3 rounded-lg border border-border bg-card p-4">
			<div className="flex items-start gap-3">
				<FileText className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
				<div className="min-w-0">
					<h3 className="m-0 text-[15px] font-semibold">{page.title}</h3>
					<p
						data-testid={`legal-status-${page.slug}`}
						className={page.company_changed ? "m-0 text-[13px] text-warning" : "m-0 text-[13px] text-muted-foreground"}
					>
						{pageStatus(page)}
					</p>
				</div>
			</div>
			<div className="flex flex-wrap gap-2">
				<Button size="sm" data-testid={`legal-edit-${page.slug}`} onClick={onEdit}>
					{t.card.edit}
				</Button>
				{live ? (
					<Button asChild variant="outline" size="sm">
						<a href={enginePageUrl(currentRagUrl(), page.slug)} target="_blank" rel="noopener noreferrer">
							{t.card.view}
							<ExternalLink aria-hidden />
						</a>
					</Button>
				) : null}
			</div>
		</li>
	);
}

function Pages({ onEdit }: { onEdit: (slug: string) => void }) {
	const q = useQuery(legalListQuery);
	if (q.isPending) return <CardSkeleton lines={6} />;
	if (q.isError) return <ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />;
	return (
		<ul className="m-0 grid list-none gap-3 p-0 min-[861px]:grid-cols-2" data-testid="legal-pages">
			{q.data.map((p) => (
				<PageCard key={p.slug} page={p} onEdit={() => onEdit(p.slug)} />
			))}
		</ul>
	);
}

function Details() {
	const q = useQuery(legalSettingsQuery);
	if (q.isPending) return <CardSkeleton lines={6} />;
	if (q.isError) return <ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />;
	return <LegalSettingsCard settings={q.data} />;
}

type Tab = "pages" | "details" | "acceptances";

/**
 * `/admin/legal`: the eight public pages (Terms, Privacy and six more): edit from the shipped
 * template, preview, publish versions; the details the pages need; and who accepted what (P3).
 */
export function LegalAdminPage() {
	const [tab, setTab] = useState<Tab>("pages");
	const [editing, setEditing] = useState<string | null>(null);
	return (
		<>
			<PageHeader title={t.title} description={t.lead} />
			<Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="gap-4">
				<TabsList aria-label={t.tabs.label} className="h-auto! flex-wrap justify-start">
					<TabsTrigger value="pages" className="flex-none px-3 py-1.5">
						{t.tabs.pages}
					</TabsTrigger>
					<TabsTrigger value="details" className="flex-none px-3 py-1.5">
						{t.tabs.details}
					</TabsTrigger>
					<TabsTrigger value="acceptances" className="flex-none px-3 py-1.5">
						{t.tabs.acceptances}
					</TabsTrigger>
				</TabsList>
				<TabsContent value="pages">
					{editing ? (
						<LegalEditor
							slug={editing}
							onBack={() => setEditing(null)}
							onFixDetails={() => {
								setEditing(null);
								setTab("details");
							}}
						/>
					) : (
						<Pages onEdit={setEditing} />
					)}
				</TabsContent>
				<TabsContent value="details">
					<Details />
				</TabsContent>
				<TabsContent value="acceptances">
					<AcceptancesTable />
				</TabsContent>
			</Tabs>
		</>
	);
}
