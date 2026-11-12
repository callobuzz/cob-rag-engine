import { CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { messageFor, type UiMessage } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { scraperUrl } from "@/lib/api/scraper";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { crawlerStrings as t } from "@/lib/strings/crawler";
import { en, fill } from "@/lib/strings/en";

/**
 * Crawler errors in plain words. The scraper answers with its own sentences (`detail`) and passes
 * the engine's codes through; the shared map covers the codes, this covers the crawler's cases.
 */
export function crawlerMessage(error: unknown, support?: string | null): UiMessage {
	const e: ApiError = toApiError(error);
	if (e.code === "network") return { title: fill(t.errors.network, { url: scraperUrl() }), body: t.errors.networkBody };
	if (e.status === 401 && !e.code) return { title: t.errors.oldScraper, body: t.errors.oldScraperBody };
	if (e.code) return messageFor(e, { support });
	if (e.status === 503) return { title: t.errors.engineDown, body: e.detail || undefined };
	if (e.status === 409 && /target corpus/i.test(e.detail)) return { title: t.errors.noTarget };
	if (e.status === 404 && /page/i.test(e.detail)) return { title: t.errors.pageNotFound };
	if (e.status === 404 && /corpus/i.test(e.detail)) return { title: t.errors.kbGone };
	if (e.status === 404) return { title: t.errors.notFound };
	if (e.status === 403 && /corpus/i.test(e.detail)) return { title: t.errors.forbiddenKb };
	if (e.status === 422 && e.detail) return { title: e.detail };
	return messageFor(e, { support });
}

/** Mirrors `ErrorState` (inside the card that failed) with crawler wording. */
export function CrawlerError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
	const { supportEmail } = useBranding();
	const msg = crawlerMessage(error, supportEmail);
	return (
		<div
			role="alert"
			className="flex items-start gap-3 rounded-md bg-destructive-soft p-3 text-[13px] text-destructive"
		>
			<CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
			<div className="grid flex-1 gap-1">
				<b className="font-semibold">{msg.title}</b>
				{msg.body ? <span>{msg.body}</span> : null}
			</div>
			{onRetry ? (
				<Button size="sm" variant="outline" onClick={onRetry}>
					{en.common.retry}
				</Button>
			) : null}
		</div>
	);
}
