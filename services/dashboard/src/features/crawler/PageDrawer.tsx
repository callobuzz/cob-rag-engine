import { useQuery } from "@tanstack/react-query";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import type { ScrapePage } from "@/lib/api/scraper-types";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { fill } from "@/lib/strings/en";
import { pageQuery } from "./api";
import { CrawlerError } from "./CrawlerError";
import { PageStatusBadge, pageTitle } from "./PagesTable";

/**
 * One page (D8 "PageDrawer"): a dialog named by the page title with its details and the extracted
 * Markdown as preformatted text (never rendered as HTML) in a scrollable, focusable region.
 */
export function PageDrawer({
	jobId,
	pageId,
	summary,
	onClose,
}: {
	jobId: string;
	pageId: string | null;
	/** The row from the table, shown while the full page loads. */
	summary: ScrapePage | null;
	onClose: () => void;
}) {
	return (
		<Sheet open={!!pageId} onOpenChange={(o) => !o && onClose()}>
			<SheetContent side="right" className="w-[560px] max-w-[94vw] gap-0 overflow-y-auto p-0">
				{pageId ? <PageBody jobId={jobId} pageId={pageId} summary={summary} /> : null}
			</SheetContent>
		</Sheet>
	);
}

function PageBody({ jobId, pageId, summary }: { jobId: string; pageId: string; summary: ScrapePage | null }) {
	const q = useQuery(pageQuery(jobId, pageId));
	const page = q.data ?? summary;
	const title = page ? pageTitle(page) : t.pages.untitled;
	return (
		<>
			<SheetHeader className="border-b border-border p-4">
				<SheetTitle className="pr-8 break-words">{title}</SheetTitle>
				<SheetDescription asChild>
					<div>{page ? <PageStatusBadge status={page.status} /> : null}</div>
				</SheetDescription>
			</SheetHeader>
			<div className="grid gap-4 p-4">
				{q.isError && !page ? <CrawlerError error={q.error} onRetry={() => void q.refetch()} /> : null}
				{page ? (
					<div className="rounded-md border border-border">
						<KeyValue
							rows={[
								{
									label: t.drawer.url,
									value: (
										<a href={page.url} target="_blank" rel="noopener noreferrer" className="break-all">
											{page.url}
										</a>
									),
								},
								{ label: t.drawer.words, value: formatNumber(page.word_count) },
								{ label: t.drawer.fetched, value: formatDateTime(page.fetched_at) },
								{ label: t.drawer.extracted, value: formatDateTime(page.extracted_at) },
								...(page.rejection_reason ? [{ label: t.drawer.rejection, value: page.rejection_reason }] : []),
								...(page.delivery_error
									? [
											{
												label: t.drawer.deliveryError,
												value: <span className="text-destructive">{page.delivery_error}</span>,
											},
										]
									: []),
							]}
						/>
					</div>
				) : null}
				<section className="grid gap-2">
					<h3 className="text-[14px] font-bold">{t.drawer.markdown}</h3>
					{q.isPending ? (
						<CardSkeleton lines={4} />
					) : q.isError ? (
						page ? (
							<CrawlerError error={q.error} onRetry={() => void q.refetch()} />
						) : null
					) : q.data?.content_file ? (
						<section
							aria-label={fill(t.drawer.markdownLabel, { title })}
							// biome-ignore lint/a11y/noNoninteractiveTabindex: a scrollable region must be keyboard-reachable (WCAG 2.1.1)
							tabIndex={0}
							data-testid="page-markdown"
							className="max-h-[55vh] overflow-auto rounded-md border border-border bg-muted p-3 focus-visible:outline-2 focus-visible:outline-ring"
						>
							<pre className="m-0 font-mono text-[12.5px] leading-relaxed break-words whitespace-pre-wrap">
								{q.data.content_file}
							</pre>
						</section>
					) : (
						<p className="m-0 text-[13px] text-muted-foreground">{t.drawer.noContent}</p>
					)}
				</section>
			</div>
		</>
	);
}
