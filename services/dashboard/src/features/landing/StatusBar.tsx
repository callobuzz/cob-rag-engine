import { ExternalLink, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { useLandingSettings } from "./api";
import { useDraft } from "./DraftProvider";

/** Why the website cannot be switched on yet, or null. */
export function switchReason(state: {
	published: unknown;
	address_effective: string | null;
	enabled: boolean;
}): string | null {
	if (state.enabled) return null;
	if (state.published == null) return t.status.needPublish;
	if (state.address_effective == null) return t.status.needAddress;
	return null;
}

/** The top of the page: on/off, address, last publish, and the save, preview and publish buttons. */
export function StatusBar({ onPreview, onPublish }: { onPreview?: () => void; onPublish?: () => void }) {
	const draft = useDraft();
	const { state } = draft;
	const settings = useLandingSettings();
	const reason = switchReason(state);

	const toggle = async (enabled: boolean) => {
		try {
			await settings.mutateAsync({ enabled });
			toast.success(enabled ? t.status.switchedOn : t.status.switchedOff);
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		}
	};

	return (
		<div data-testid="landing-status" className="grid gap-3 rounded-lg border border-border bg-card p-4">
			<div className="flex flex-wrap items-center gap-x-6 gap-y-3">
				<div className="flex items-center gap-3">
					<Switch
						id="landing-on"
						checked={state.enabled}
						disabled={reason != null || settings.isPending}
						aria-describedby={reason ? "landing-on-reason" : undefined}
						onCheckedChange={(v) => void toggle(v)}
					/>
					<div className="grid">
						<label htmlFor="landing-on" className="text-[14px] font-semibold">
							{t.status.switchLabel}
						</label>
						<span
							data-testid="landing-on-state"
							className={
								state.enabled ? "text-[12.5px] font-medium text-success" : "text-[12.5px] text-muted-foreground"
							}
						>
							{state.enabled ? t.status.on : t.status.off}
						</span>
					</div>
				</div>
				<div className="grid min-w-0 text-[13px]">
					<span className="text-muted-foreground">{t.status.address}</span>
					<span className="truncate font-medium" data-testid="landing-address">
						{state.address_effective ?? t.status.noAddress}
					</span>
				</div>
				<div className="grid text-[13px]">
					<span className="text-muted-foreground">
						{state.published
							? fill(t.status.lastPublished, {
									date: formatDateTime(state.published.published_at),
									name: state.published.published_by,
								})
							: t.status.neverPublished}
					</span>
					{state.enabled && state.address_effective ? (
						<a
							href={state.address_effective}
							target="_blank"
							rel="noopener noreferrer"
							className="inline-flex w-fit items-center gap-1 font-medium"
						>
							{t.status.view}
							<ExternalLink className="size-3.5" aria-hidden />
						</a>
					) : null}
				</div>
				<div className="ml-auto flex flex-wrap items-center gap-2">
					{draft.dirty ? <span className="text-[12.5px] text-muted-foreground">{t.status.unsaved}</span> : null}
					<Button
						variant="outline"
						data-testid="landing-save"
						disabled={!draft.dirty || draft.saving}
						onClick={() => void draft.save()}
					>
						{draft.saving ? t.status.saving : t.status.save}
					</Button>
					{onPreview ? (
						<Button variant="outline" data-testid="landing-preview" onClick={onPreview}>
							{t.status.preview}
						</Button>
					) : null}
					{onPublish ? (
						<Button data-testid="landing-publish" onClick={onPublish}>
							{t.status.publish}
						</Button>
					) : null}
				</div>
			</div>
			{reason ? (
				<p id="landing-on-reason" className="m-0 text-[12.5px] text-muted-foreground">
					{reason}
				</p>
			) : null}
			{draft.conflict ? (
				<div
					role="alert"
					data-testid="landing-conflict"
					className="flex flex-wrap items-start gap-3 rounded-sm bg-warning-soft p-3 text-[13px] text-warning"
				>
					<TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
					<div className="grid min-w-0 flex-1 gap-0.5">
						<strong>{t.status.conflictTitle}</strong>
						<span>{t.status.conflictBody}</span>
					</div>
					<Button size="sm" variant="outline" onClick={() => void draft.reload()}>
						{t.status.reload}
					</Button>
				</div>
			) : null}
		</div>
	);
}
