import { useQuery } from "@tanstack/react-query";
import { CheckCheck, RotateCw, Send } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import type { DeliverResponse, ScrapeJobDetail } from "@/lib/api/scraper-types";
import { formatNumber } from "@/lib/format/number";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { fill } from "@/lib/strings/en";
import { pagesQuery, useApprove, useDeliver } from "./api";
import { crawlerMessage } from "./CrawlerError";
import { isJobActive } from "./statuses";

/**
 * "Approve all verified ({n})" and "Deliver {m} approved pages". A delivery answers
 * attempted / delivered / failed; failed pages stay approved with `delivery_error`, and the
 * button becomes "Retry delivery" (the scraper re-sends only what is still approved).
 */
export function DeliverBar({ job, kbName }: { job: ScrapeJobDetail; kbName: string }) {
	const verified = job.page_counts.verified ?? 0;
	const approved = job.page_counts.approved ?? 0;
	const approvedPages = useQuery({ ...pagesQuery(job.id, "approved", isJobActive(job)), enabled: approved > 0 });
	const failedBefore = (approvedPages.data ?? []).filter((p) => p.delivery_error).length;
	const approve = useApprove(job.id);
	const deliver = useDeliver(job.id, job.target_corpus_id || null);
	const [result, setResult] = useState<DeliverResponse | null>(null);
	const noTarget = !job.target_corpus_id;
	const retry = failedBefore > 0 && failedBefore === approved;

	const approveAll = () =>
		approve.mutate(
			{ approve_all_verified: true },
			{
				onSuccess: (r) =>
					toast.success(
						r.approved === 1 ? t.pages.approvedToastOne : fill(t.pages.approvedToast, { n: formatNumber(r.approved) }),
					),
			},
		);
	const send = () => {
		setResult(null);
		deliver.mutate(undefined, { onSuccess: (r) => setResult(r) });
	};

	const deliverLabel = deliver.isPending
		? t.deliver.delivering
		: retry
			? fill(t.deliver.retry, { n: formatNumber(approved) })
			: approved === 0
				? t.deliver.deliverNone
				: approved === 1
					? t.deliver.deliverOne
					: fill(t.deliver.deliver, { n: formatNumber(approved) });

	return (
		<section aria-label={t.deliver.label} className="grid gap-3 rounded-lg border border-border bg-card p-4">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<p className="m-0 max-w-[520px] text-[13px] text-foreground-2">{fill(t.deliver.hint, { kb: kbName })}</p>
				<div className="flex flex-wrap gap-2">
					<Button variant="outline" disabled={verified === 0 || approve.isPending} onClick={approveAll}>
						<CheckCheck aria-hidden />
						{approve.isPending ? t.deliver.approving : fill(t.deliver.approveAll, { n: formatNumber(verified) })}
					</Button>
					<Button variant="default" disabled={approved === 0 || noTarget || deliver.isPending} onClick={send}>
						{retry ? <RotateCw aria-hidden /> : <Send aria-hidden />}
						{deliverLabel}
					</Button>
				</div>
			</div>
			{noTarget ? <FormAlert tone="warn">{t.job.legacy}</FormAlert> : null}
			{approve.error ? <FormAlert>{crawlerMessage(approve.error).title}</FormAlert> : null}
			{deliver.error ? (
				<FormAlert>
					<b className="font-semibold">{crawlerMessage(deliver.error).title}</b>
					{crawlerMessage(deliver.error).body ? (
						<span className="block">{crawlerMessage(deliver.error).body}</span>
					) : null}
				</FormAlert>
			) : null}
			{result ? (
				<FormAlert tone={result.failed ? "warn" : "info"} className="items-start">
					<span data-testid="deliver-result" role="status">
						<b className="font-semibold">
							{fill(t.deliver.result, {
								delivered: formatNumber(result.delivered),
								attempted: formatNumber(result.attempted),
							})}
						</b>{" "}
						{result.failed
							? fill(t.deliver.resultFailed, { failed: formatNumber(result.failed) })
							: fill(t.deliver.allDone, { kb: kbName })}
					</span>
				</FormAlert>
			) : null}
		</section>
	);
}
