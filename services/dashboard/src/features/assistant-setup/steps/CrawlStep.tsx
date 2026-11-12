import { LoaderCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { CrawlerError } from "@/features/crawler/CrawlerError";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { setupStrings as t } from "@/lib/strings/setup";
import { useProposeSetup } from "../api";
import { CrawlTimeoutError, crawlSite, deliverCrawl, discardCrawl } from "../crawl";

/** The engine's default `RAG_SETUP_MAX_PAGES`; the proposal call reports a 422 if it was lowered. */
const MAX_PAGES = 15;

type Phase = "crawling" | "proposing" | "failed";
type Failure = "noModel" | "noPages" | "timedOut" | "cancelled" | { error: unknown };

/**
 * Reads the website through the scraper service (never the engine), then asks the engine for a
 * proposal from the page text. The pages are thrown away afterwards unless the owner ticked
 * "Also add these pages". Failing in any way leaves a way out: pick a business type instead.
 */
export function CrawlStep({
	kb,
	sid,
	url,
	onTypeInstead,
}: {
	kb: string;
	sid: string;
	url: string;
	onTypeInstead: () => void;
}) {
	const propose = useProposeSetup(kb, sid);
	const [phase, setPhase] = useState<Phase>("crawling");
	const [progress, setProgress] = useState<{ found: number; done: number } | null>(null);
	const [failure, setFailure] = useState<Failure | null>(null);
	const [alsoAdd, setAlsoAdd] = useState(false);
	const alsoAddRef = useRef(false);
	const control = useRef<AbortController | null>(null);
	const [attempt, setAttempt] = useState(0);
	const startedAttempt = useRef(-1);
	const mounted = useRef(false);
	const proposeRef = useRef(propose.mutateAsync);
	proposeRef.current = propose.mutateAsync;

	const run = useCallback(
		async (signal: AbortSignal) => {
			let jobId: string | null = null;
			let delivered = false;
			try {
				const crawled = await crawlSite({ url, kb, maxPages: MAX_PAGES, onProgress: setProgress, signal });
				jobId = crawled.jobId;
				if (crawled.pages.length === 0) {
					setFailure("noPages");
					setPhase("failed");
					return;
				}
				setPhase("proposing");
				await proposeRef.current({ body: { crawl_job_id: crawled.jobId, pages: crawled.pages }, signal });
				if (alsoAddRef.current) {
					const out = await deliverCrawl(crawled.jobId);
					delivered = true;
					toast.success(fill(t.crawl.addedToast, { delivered: out.delivered, failed: out.failed }));
				}
			} catch (e) {
				if (
					signal.aborted ||
					(e instanceof DOMException && e.name === "AbortError") ||
					toApiError(e).code === "aborted"
				)
					setFailure("cancelled");
				else if (e instanceof CrawlTimeoutError) setFailure("timedOut");
				else if (toApiError(e).code === "assistant_llm_unavailable") setFailure("noModel");
				else setFailure({ error: e });
				setPhase("failed");
			} finally {
				if (jobId && !delivered) await discardCrawl(jobId);
			}
		},
		[kb, url],
	);

	// One crawl per attempt. React's development double-mount runs this effect twice: the ref keeps
	// the second run from starting another job, and the abort waits a tick so a re-mount survives
	// it while a real unmount still stops the crawl.
	useEffect(() => {
		mounted.current = true;
		if (startedAttempt.current !== attempt) {
			startedAttempt.current = attempt;
			const controller = new AbortController();
			control.current = controller;
			setPhase("crawling");
			setFailure(null);
			setProgress(null);
			void run(controller.signal);
		}
		return () => {
			mounted.current = false;
			setTimeout(() => {
				if (!mounted.current) control.current?.abort();
			}, 0);
		};
	}, [run, attempt]);

	if (phase === "failed" && failure) {
		const named = typeof failure === "string" ? failure : null;
		return (
			<div className="grid gap-4 rounded-lg border border-border bg-card p-5">
				<h2 className="m-0 text-lg font-semibold">{t.crawl.heading}</h2>
				{named ? (
					<p role="alert" className="m-0 rounded-md bg-destructive-soft p-3 text-[13px] text-destructive">
						{t.crawl[named]}
					</p>
				) : (
					<CrawlerError error={(failure as { error: unknown }).error} />
				)}
				<div className="flex flex-wrap gap-2">
					<Button variant="default" onClick={onTypeInstead}>
						{t.crawl.typeInstead}
					</Button>
					{named !== "noModel" ? (
						<Button variant="outline" onClick={() => setAttempt((n) => n + 1)}>
							{t.crawl.retry}
						</Button>
					) : null}
				</div>
			</div>
		);
	}

	return (
		<div className="grid gap-4 rounded-lg border border-border bg-card p-5">
			<h2 className="m-0 text-lg font-semibold">{t.crawl.heading}</h2>
			<p role="status" className="m-0 flex items-center gap-2 text-[14px]">
				<LoaderCircle className="size-4 animate-spin" aria-hidden />
				{phase === "proposing"
					? t.crawl.proposing
					: progress
						? fill(t.crawl.progress, { done: progress.done, found: progress.found })
						: t.crawl.starting}
			</p>
			<div className="flex items-start gap-2.5">
				<Checkbox
					id="setup-also-add"
					checked={alsoAdd}
					onCheckedChange={(v) => {
						alsoAddRef.current = v === true;
						setAlsoAdd(v === true);
					}}
					className="mt-0.5"
				/>
				<div className="grid gap-0.5">
					<label htmlFor="setup-also-add" className="text-[13.5px] font-semibold">
						{t.crawl.alsoAdd}
					</label>
					<span className="text-[12.5px] text-muted-foreground">{t.crawl.alsoAddHint}</span>
				</div>
			</div>
			<div className="flex flex-wrap gap-2">
				<Button variant="outline" onClick={() => control.current?.abort()}>
					{t.crawl.cancel}
				</Button>
			</div>
		</div>
	);
}
