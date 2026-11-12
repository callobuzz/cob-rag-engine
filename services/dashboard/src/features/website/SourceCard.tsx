import { Globe, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { websiteStrings } from "@/lib/strings/website";
import { isBusy, useLearnWebsite, useRemoveSource, type WebsiteSource } from "./api";
import { siteLabel, sourceNote, sourceStatusWord, sourceTone } from "./words";

const t = websiteStrings.sources;

function toastError(e: unknown) {
	const m = messageFor(toApiError(e));
	toast.error(m.title, m.body ? { description: m.body } : undefined);
}

/** One website the assistant reads: how far it is, when it last read it, and removing it. */
export function SourceCard({ kb, source }: { kb: string; source: WebsiteSource }) {
	const remove = useRemoveSource(kb);
	const [confirming, setConfirming] = useState(false);
	const site = siteLabel(source.origin);
	const busy = isBusy(source);
	const done = source.pages_learned + source.pages_skipped;
	const percent = source.pages_found > 0 ? Math.min(100, Math.round((done / source.pages_found) * 100)) : 0;
	const note = sourceNote(source);

	return (
		<article
			aria-label={site}
			className="grid gap-2 rounded-lg border border-border bg-card p-4"
			data-testid="website-source"
		>
			<div className="flex flex-wrap items-start justify-between gap-3">
				<div className="flex min-w-0 items-center gap-2">
					<Globe className="size-4 shrink-0 text-muted-foreground" aria-hidden />
					<h3 className="truncate text-[15px] font-bold">{site}</h3>
					<Badge variant={sourceTone(source)}>{sourceStatusWord(source)}</Badge>
				</div>
				<OwnerGate action="website.manage">
					<Button
						size="sm"
						variant="ghost"
						aria-label={fill(t.removeFor, { site })}
						onClick={() => setConfirming(true)}
					>
						<Trash2 aria-hidden />
						{t.remove}
					</Button>
				</OwnerGate>
			</div>
			<div className="text-[13px] text-foreground-2">
				{source.pages_found > 0
					? fill(t.pagesOfFound, {
							learned: formatNumber(source.pages_learned),
							found: formatNumber(source.pages_found),
						})
					: fill(t.learnedOnly, { learned: formatNumber(source.pages_learned) })}
				{" · "}
				{source.last_run_finished_at
					? fill(t.lastRead, { when: formatRelative(source.last_run_finished_at) })
					: t.neverRead}
			</div>
			{busy ? (
				<div
					role="progressbar"
					aria-label={fill(t.progressLabel, { site })}
					aria-valuemin={0}
					aria-valuemax={100}
					aria-valuenow={percent}
					className="h-1.5 overflow-hidden rounded-full bg-secondary"
				>
					<div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
				</div>
			) : null}
			{note ? <p className="m-0 text-[13px] text-muted-foreground">{note}</p> : null}
			<TypedConfirmDialog
				open={confirming}
				onOpenChange={setConfirming}
				title={fill(t.removeTitle, { site })}
				body={t.removeBody}
				word={site}
				confirmLabel={t.removeConfirm}
				pending={remove.isPending}
				onConfirm={() =>
					remove.mutate(source.id, {
						onSuccess: () => {
							setConfirming(false);
							toast.success(fill(t.removed, { site }));
						},
						onError: (e) => {
							setConfirming(false);
							toastError(e);
						},
					})
				}
			/>
		</article>
	);
}

/** An allowed site of the widget that has not been read yet. */
export function UnlearnedCard({ kb, origin }: { kb: string; origin: string }) {
	const learn = useLearnWebsite(kb);
	const site = siteLabel(origin);
	return (
		<article
			aria-label={site}
			className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed border-input bg-card p-4"
			data-testid="website-unlearned"
		>
			<div className="min-w-0">
				<h3 className="truncate text-[15px] font-bold">{site}</h3>
				<p className="m-0 text-[13px] text-muted-foreground">{fill(t.unlearnedBody, { site })}</p>
			</div>
			<Button
				size="sm"
				disabled={learn.isPending}
				aria-label={fill(t.learnFor, { site })}
				onClick={() =>
					learn.mutate(origin, {
						onSuccess: () => toast.success(fill(t.started, { site })),
						onError: toastError,
					})
				}
			>
				{learn.isPending ? t.starting : t.learn}
			</Button>
		</article>
	);
}
