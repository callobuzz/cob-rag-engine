import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { type LandingVersion, useRestore, useVersions } from "./api";
import { useDraft } from "./DraftProvider";

/** Every published version, newest first. Restoring copies one into the draft; it is not live until published again. */
export function VersionsTab() {
	const draft = useDraft();
	const q = useVersions();
	const restore = useRestore();
	const [asking, setAsking] = useState<LandingVersion | null>(null);
	const [error, setError] = useState<string | null>(null);
	const live = draft.state.published?.version;

	const run = async () => {
		if (!asking) return;
		setError(null);
		try {
			await restore.mutateAsync({ version: asking.version, base_revision: draft.state.draft_revision });
			toast.success(fill(t.versions.restored, { version: asking.version }));
			setAsking(null);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	if (q.isPending) return <CardSkeleton lines={4} />;
	if (q.isError) return <ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />;
	return (
		<section
			aria-label={t.versions.title}
			className="grid gap-3 rounded-lg border border-border bg-card p-4"
			data-testid="versions-tab"
		>
			<div className="grid gap-0.5">
				<h2 className="m-0 text-[15px] font-bold">{t.versions.title}</h2>
				<p className="m-0 text-[13px] text-muted-foreground">{t.versions.about}</p>
			</div>
			{q.data.length === 0 ? (
				<p className="m-0 text-[13px] text-muted-foreground">{t.versions.none}</p>
			) : (
				<ul className="m-0 grid list-none gap-2 p-0">
					{q.data.map((v) => (
						<li
							key={v.version}
							data-testid={`version-${v.version}`}
							className="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-border p-3"
						>
							<div className="grid min-w-0">
								<strong className="text-[14px]">
									{fill(t.versions.version, { version: v.version })}
									{v.version === live ? (
										<span className="ml-2 text-[12.5px] font-medium text-success">{t.versions.live}</span>
									) : null}
								</strong>
								<span className="text-[12.5px] text-muted-foreground">
									{formatDateTime(v.published_at)} · {v.published_by}
								</span>
								{v.change_note ? <span className="text-[13px]">{v.change_note}</span> : null}
							</div>
							<Button
								type="button"
								size="sm"
								variant="outline"
								aria-label={fill(t.versions.restoreFor, { version: v.version })}
								onClick={() => setAsking(v)}
							>
								{t.versions.restore}
							</Button>
						</li>
					))}
				</ul>
			)}
			<ConfirmDialog
				open={asking != null}
				onOpenChange={(o) => {
					if (!o) {
						setAsking(null);
						setError(null);
					}
				}}
				title={fill(t.versions.confirmTitle, { version: asking?.version ?? "" })}
				body={draft.dirty ? t.versions.confirmDirty : t.versions.confirmBody}
				confirmLabel={t.versions.restore}
				destructive={false}
				pending={restore.isPending}
				onConfirm={() => void run()}
			>
				{error ? (
					<p role="alert" className="m-0 text-[13px] text-destructive">
						{error}
					</p>
				) : null}
			</ConfirmDialog>
		</section>
	);
}
