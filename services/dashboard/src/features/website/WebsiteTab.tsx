import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react"; // BIZ2
import { toast } from "sonner";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { formatDuration, messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { websiteStrings as t } from "@/lib/strings/website";
import { isBusy, useRereadWebsite, websiteKey, websiteQuery } from "./api";
import { PushLinkCard } from "./freshness/PushLinkCard"; // BIZ2
import { RefreshSettings } from "./freshness/RefreshSettings"; // BIZ2
import { LearningSettings } from "./LearningSettings";
import { PagesTable } from "./PagesTable";
import { SourceCard, UnlearnedCard } from "./SourceCard";

/**
 * "From your website" (BIZ1): the sites the assistant reads on its own, how far each one is, the
 * pages and what happened to each, and the owner's controls. Mounted by the Knowledge page.
 */
export function WebsiteTab({ kbId }: { kbId: string }) {
	const { productName } = useBranding();
	const site = useQuery(websiteQuery(kbId));
	const reread = useRereadWebsite(kbId);
	// BIZ2: when a site stops being read, the page list is looked at once more (it stopped polling)
	const queryClient = useQueryClient();
	const wasBusy = useRef(false);
	const busyNow = site.data?.sources.some(isBusy) ?? false;
	useEffect(() => {
		if (wasBusy.current && !busyNow) void queryClient.invalidateQueries({ queryKey: [...websiteKey(kbId), "pages"] });
		wasBusy.current = busyNow;
	}, [busyNow, kbId, queryClient]);
	// /BIZ2

	if (site.isPending) return <CardSkeleton lines={4} />;
	if (site.isError) {
		return <ErrorState error={site.error} title={t.loadError} onRetry={() => void site.refetch()} />;
	}
	const data = site.data;
	const busy = data.sources.some(isBusy);
	const nothing = data.sources.length === 0 && data.unlearned_origins.length === 0;

	const readAgain = () =>
		reread.mutate(undefined, {
			onSuccess: () => toast.success(t.pages.rereadAllQueued),
			onError: (e) => {
				const err = toApiError(e);
				if (err.code === "website_reread_too_soon" && err.retryAfterS != null) {
					toast(fill(t.pages.rereadTooSoon, { wait: formatDuration(err.retryAfterS) }));
					return;
				}
				const m = messageFor(err);
				toast.error(m.title, m.body ? { description: m.body } : undefined);
			},
		});

	if (!data.plan.learning_enabled) {
		return (
			<EmptyState
				title={t.title}
				body={fill(t.planOff, { product: productName })}
				art={<Globe className="size-10 text-muted-foreground" aria-hidden />}
			/>
		);
	}

	return (
		<div className="grid gap-4">
			<Panel
				title={t.title}
				sub={t.lead}
				aside={
					data.sources.length > 0 ? (
						<Button size="sm" variant="outline" disabled={reread.isPending || busy} onClick={readAgain}>
							<RefreshCw aria-hidden />
							{t.pages.rereadAll}
						</Button>
					) : undefined
				}
			>
				<div className="grid gap-3 p-4">
					{!data.settings.enabled ? <p className="m-0 text-[13px] text-muted-foreground">{t.kbOff}</p> : null}
					{nothing ? (
						<EmptyState
							title={t.noSitesTitle}
							body={t.noSitesBody}
							art={<Globe className="size-10 text-muted-foreground" aria-hidden />}
						/>
					) : null}
					{data.sources.map((s) => (
						<SourceCard key={s.id} kb={kbId} source={s} />
					))}
					{data.unlearned_origins.map((origin) => (
						<UnlearnedCard key={origin} kb={kbId} origin={origin} />
					))}
				</div>
			</Panel>
			{data.sources.length > 0 ? (
				<>
					<LearningSettings kb={kbId} settings={data.settings} />
					{/* BIZ2 */}
					<RefreshSettings kb={kbId} settings={data.settings} />
					<PushLinkCard kb={kbId} learnKey={data.learn_key ?? { set: false }} />
					{/* /BIZ2 */}
					<PagesTable kb={kbId} poll={busy} />
				</>
			) : null}
		</div>
	);
}
